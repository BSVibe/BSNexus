"""Multi-aspect verifier — Phase 1 (C-shape rollout).

A deliverable's overall ``proof_state`` is the roll-up of N
:class:`VerificationAspect` rows (one per discrete verification
dimension). Today's aspects:

  - ``code_test``: ``python -m pytest`` (existing behaviour;
    activation rule = ``select_proof_policy.python_policy`` from the
    pre-refactor module — pyproject / pytest.ini / tox.ini / setup.cfg
    / ``tests/`` dir / root ``test_*.py`` or ``*_test.py``).
  - ``code_lint``: ``ruff check .`` + ``ruff format --check .``
    (activation rule = pyproject.toml declares ruff as a dev dep).
  - ``code_install_smoke``: fresh venv + ``pip install -e .`` +
    ``python -c "import <module>"`` (activation rule = pyproject.toml
    exists with declared deps). Catches the
    "import works in workspace but fails on a clean install" class
    of bug (e.g. heartline's missing-httpx in pyproject).

Aspects run sequentially in one worker today; per-aspect queue
topology is a Phase 2 follow-up (no schema change required).

Adding a new aspect type = add an enum value + an activation rule +
an aspect-runner entry. No changes to the worker, brief, PR body, or
roll-up needed.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.domain import (
    DeliverableStatus,
    DeliverableType,
    ProofAspectStatus,
    ProofAspectType,
    ProofState,
)
from backend.src.models import Deliverable, VerificationAspect

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class AspectSpec:
    """In-memory description of a single aspect — what to run, the
    refs/timeout it expects, whether it blocks ``verified``."""

    aspect_type: ProofAspectType
    commands: tuple[tuple[str, ...], ...]
    required_refs: tuple[str, ...] = ()
    timeout_s: int = 300
    blocking: bool = True

    def to_inputs(self, workspace_root: Path, changed_files: Sequence[str]) -> dict[str, Any]:
        return {
            "workspace_root": str(workspace_root),
            "changed_files": list(changed_files),
            "commands": [list(cmd) for cmd in self.commands],
            "required_refs": list(self.required_refs),
            "timeout_s": self.timeout_s,
        }


# ──────────────────────────── public entrypoint ──────────────────────────


async def run_verification(
    *,
    deliverable: Deliverable,
    workspace_root: Path | str,
    session: AsyncSession,
    changed_files: Sequence[str] = (),
) -> None:
    """Generate the applicable aspects, run them in sequence, roll up
    to ``deliverable.proof_state``.

    Soft contract: if no aspects apply, the deliverable falls to
    ``human_review_required`` (matches the pre-refactor ``no_policy``
    behaviour). Roll-up never crashes the caller — any infrastructural
    error in an aspect runner is caught as
    ``ProofAspectStatus.error`` (also human_review_required at
    deliverable level)."""
    root = Path(workspace_root)
    specs = select_verification_aspects(
        workspace_root=root,
        deliverable_type=deliverable.type,
        changed_files=changed_files,
    )

    if not specs:
        deliverable.proof_state = ProofState.human_review_required
        await session.flush()
        return

    # Mark verifying while aspects run — the worker may be processing
    # for tens of seconds (install_smoke alone is 30-90s) and any open
    # UI/SSE subscriber should see the in-flight state.
    deliverable.proof_state = ProofState.verifying
    deliverable.status = DeliverableStatus.verifying
    await session.flush()

    aspects: list[VerificationAspect] = []
    for spec in specs:
        aspect = VerificationAspect(
            deliverable_id=deliverable.id,
            aspect_type=spec.aspect_type,
            status=ProofAspectStatus.queued,
            inputs=spec.to_inputs(root, changed_files),
            blocking=spec.blocking,
        )
        session.add(aspect)
        aspects.append(aspect)
    await session.flush()

    async with _aspect_venv(root, specs) as (venv_python, venv_error):
        for spec, aspect in zip(specs, aspects, strict=True):
            aspect.status = ProofAspectStatus.running
            aspect.started_at = datetime.now(UTC)
            await session.flush()

            status, summary, exit_code = await _run_one_aspect(
                spec=spec,
                root=root,
                venv_python=venv_python,
                venv_error=venv_error,
                deliverable_id=str(deliverable.id),
            )

            aspect.status = status
            aspect.result_summary = (summary or "")[:4000]
            aspect.exit_code = exit_code
            aspect.completed_at = datetime.now(UTC)
            await session.flush()

    deliverable.proof_state = rollup_proof_state(aspects)
    if deliverable.proof_state == ProofState.verified:
        deliverable.status = DeliverableStatus.review_ready
    await session.flush()


def rollup_proof_state(aspects: Sequence[VerificationAspect]) -> ProofState:
    """Deterministic roll-up.

    Rules (in order):
      1. No aspects at all → ``human_review_required`` (no_policy path)
      2. Any blocking aspect ``error`` → ``human_review_required``
         (infra failure shouldn't count against the model)
      3. Any blocking aspect ``failed`` → ``verification_failed``
      4. Any blocking aspect still ``queued`` / ``running`` →
         ``verifying`` (caller didn't drain everything yet)
      5. At least one blocking aspect ``passed`` AND no ``failed`` →
         ``verified``
      6. Else → ``human_review_required``
    """
    if not aspects:
        return ProofState.human_review_required
    blocking = [a for a in aspects if a.blocking]
    if not blocking:
        # Only non-blocking aspects — treat as human_review_required so
        # an all-optional-aspect deliverable doesn't auto-ship.
        return ProofState.human_review_required
    statuses = [a.status for a in blocking]
    if any(s == ProofAspectStatus.error for s in statuses):
        return ProofState.human_review_required
    if any(s == ProofAspectStatus.failed for s in statuses):
        return ProofState.verification_failed
    if any(s in {ProofAspectStatus.queued, ProofAspectStatus.running} for s in statuses):
        return ProofState.verifying
    if any(s == ProofAspectStatus.passed for s in statuses):
        return ProofState.verified
    return ProofState.human_review_required


async def latest_aspect_of_type(
    *, deliverable_id: uuid.UUID, aspect_type: ProofAspectType, session: AsyncSession
) -> VerificationAspect | None:
    """Helper for surfaces (Brief, PR body) that want the most recent
    aspect of a given type — usually ``code_test`` for the "verifier"
    line in the PR body, or any latest-of-type for the Brief shipped
    section."""
    stmt = (
        select(VerificationAspect)
        .where(
            VerificationAspect.deliverable_id == deliverable_id,
            VerificationAspect.aspect_type == aspect_type,
        )
        .order_by(VerificationAspect.created_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def aspects_for_deliverable(*, deliverable_id: uuid.UUID, session: AsyncSession) -> list[VerificationAspect]:
    stmt = (
        select(VerificationAspect)
        .where(VerificationAspect.deliverable_id == deliverable_id)
        .order_by(VerificationAspect.created_at.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


# ──────────────────────────── aspect selection ──────────────────────────


def select_verification_aspects(
    *,
    workspace_root: Path | str,
    deliverable_type: DeliverableType,
    changed_files: Sequence[str] = (),
) -> list[AspectSpec]:
    """Inspect the workspace and decide which aspects apply.

    Activation conditions are deliberate — an aspect that's wrong to
    run on this kind of deliverable is *absent*, not skipped. Skipped
    is reserved for the case where an aspect is configured to run but
    detected something it can't proceed with (today: not used)."""
    root = Path(workspace_root)
    if deliverable_type not in {DeliverableType.code, DeliverableType.pr, DeliverableType.preview}:
        return []

    specs: list[AspectSpec] = []
    test = _code_test_aspect(root, changed_files)
    if test is not None:
        specs.append(test)
    lint = _code_lint_aspect(root)
    if lint is not None:
        specs.append(lint)
    smoke = _code_install_smoke_aspect(root)
    if smoke is not None:
        specs.append(smoke)
    build = _code_build_aspect(root)
    if build is not None:
        specs.append(build)
    return specs


def _code_test_aspect(root: Path, changed_files: Sequence[str]) -> AspectSpec | None:
    """``code_test`` aspect with Python and Node-flavoured runners.

    Selection priority:
      1. Python signals (pyproject.toml / tests dir / root test_*.py /
         changed pytest file) → ``python -m pytest``
      2. ``package.json`` with a non-default test script → manager test
      3. ``package.json`` with a build script → manager build
      4. None applies → no code_test aspect (deliverable falls to
         human_review_required unless another aspect runs)
    """
    python = _python_test_runner(root, changed_files)
    if python is not None:
        return python
    return _node_test_runner(root)


def _python_test_runner(root: Path, changed_files: Sequence[str]) -> AspectSpec | None:
    refs = [name for name in ("pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg") if (root / name).exists()]
    has_tests_dir = (root / "tests").is_dir()
    root_test_files = _root_pytest_test_files(root)
    changed_test_files = [path for path in changed_files if _is_pytest_test_file(path)]
    if not refs and not has_tests_dir and not root_test_files and not changed_test_files:
        return None
    if has_tests_dir:
        refs.append("tests/")
    for tfile in (*changed_test_files, *root_test_files):
        if tfile not in refs:
            refs.append(tfile)
    return AspectSpec(
        aspect_type=ProofAspectType.code_test,
        # ``<venv_python>`` is substituted at run time with an isolated
        # venv that has the workspace installed + the verifier toolchain
        # (pytest). The prod container's own interpreter does NOT carry
        # pytest — it's a dev-only dependency — so running pytest there
        # always failed "No module named pytest" (the Cycle 7-8 bug).
        commands=(("<venv_python>", "-m", "pytest"),),
        required_refs=tuple(refs),
        timeout_s=300,
        blocking=True,
    )


def _node_test_runner(root: Path) -> AspectSpec | None:
    package_json = root / "package.json"
    if not package_json.exists():
        return None
    try:
        scripts = json.loads(package_json.read_text()).get("scripts") or {}
    except (OSError, json.JSONDecodeError):
        scripts = {}
    manager = "pnpm" if (root / "pnpm-lock.yaml").exists() else "npm"
    refs = ["package.json"]
    if manager == "pnpm":
        refs.append("pnpm-lock.yaml")
    test_script = scripts.get("test")
    if isinstance(test_script, str) and test_script and not _is_default_npm_test_script(test_script):
        return AspectSpec(
            aspect_type=ProofAspectType.code_test,
            commands=((manager, "test"),),
            required_refs=tuple(refs),
            timeout_s=300,
            blocking=True,
        )
    build_script = scripts.get("build")
    if isinstance(build_script, str) and build_script:
        command = (manager, "build") if manager == "pnpm" else ("npm", "run", "build")
        return AspectSpec(
            aspect_type=ProofAspectType.code_test,
            commands=(command,),
            required_refs=tuple(refs),
            timeout_s=300,
            blocking=True,
        )
    return None


def _is_default_npm_test_script(script: str) -> bool:
    normalized = script.strip().lower()
    return "no test specified" in normalized and normalized.startswith("echo")


def _code_lint_aspect(root: Path) -> AspectSpec | None:
    """Run ruff if the project declares it as a dev/build dep.

    Activation is conservative — only triggers when pyproject.toml
    actually has ruff in its tooling so we don't surprise repos that
    haven't opted in. Both ``ruff check`` and ``ruff format --check``
    run; first failure stops the aspect."""
    pyproject = root / "pyproject.toml"
    if not pyproject.exists() or not _pyproject_declares_ruff(pyproject):
        return None
    return AspectSpec(
        aspect_type=ProofAspectType.code_lint,
        # ``<venv_python>`` substituted at run time — see _python_test_runner.
        commands=(
            ("<venv_python>", "-m", "ruff", "check", "."),
            ("<venv_python>", "-m", "ruff", "format", "--check", "."),
        ),
        required_refs=("pyproject.toml",),
        timeout_s=60,
        blocking=True,
    )


def _code_install_smoke_aspect(root: Path) -> AspectSpec | None:
    """Activate when pyproject.toml exists with declared dependencies.

    The runner (``_run_install_smoke``) is special — it builds an
    isolated venv and tries to install + import. The ``commands`` here
    are recorded for telemetry / brief surfacing only; the actual
    work is done by the runner."""
    pyproject = root / "pyproject.toml"
    if not pyproject.exists() or not _pyproject_declares_deps(pyproject):
        return None
    return AspectSpec(
        aspect_type=ProofAspectType.code_install_smoke,
        commands=(
            ("python", "-m", "venv", "<tmpvenv>"),
            ("<tmpvenv>/bin/pip", "install", "-e", "."),
            ("<tmpvenv>/bin/python", "-c", "import <module>"),
        ),
        required_refs=("pyproject.toml",),
        timeout_s=180,
        blocking=True,
    )


def _code_build_aspect(root: Path) -> AspectSpec | None:
    """Activate when a top-level ``Dockerfile`` exists.

    The runner (``_run_docker_build``) shells out to ``docker build``.
    Skips gracefully (status=skipped, not failed) when the docker CLI
    is not available, so CI environments without a docker socket
    don't get a spurious blocking failure. Cost: 30-180s when active."""
    if not (root / "Dockerfile").exists():
        return None
    return AspectSpec(
        aspect_type=ProofAspectType.code_build,
        commands=(("docker", "build", "--quiet", "--tag", "<tmptag>", "."),),
        required_refs=("Dockerfile",),
        timeout_s=240,
        blocking=True,
    )


# ──────────────────────────── activation helpers ──────────────────────────


def _is_pytest_test_file(path: str) -> bool:
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


def _root_pytest_test_files(root: Path) -> list[str]:
    try:
        return sorted(p.name for p in root.iterdir() if p.is_file() and _is_pytest_test_file(p.name))
    except OSError:
        return []


def _pyproject_declares_ruff(pyproject: Path) -> bool:
    try:
        data = tomllib.loads(pyproject.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return False
    # PEP 621
    project_deps = (data.get("project", {}) or {}).get("optional-dependencies", {}) or {}
    for deps in project_deps.values():
        if isinstance(deps, list) and any("ruff" in str(d) for d in deps):
            return True
    # Poetry
    poetry = (data.get("tool", {}) or {}).get("poetry", {}) or {}
    for group_name in ("dependencies", "dev-dependencies"):
        if "ruff" in (poetry.get(group_name) or {}):
            return True
    for group in (poetry.get("group", {}) or {}).values():
        if isinstance(group, dict) and "ruff" in (group.get("dependencies") or {}):
            return True
    return False


def _pyproject_declares_deps(pyproject: Path) -> bool:
    try:
        data = tomllib.loads(pyproject.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return False
    if (data.get("project", {}) or {}).get("dependencies"):
        return True
    poetry_deps = (data.get("tool", {}) or {}).get("poetry", {}).get("dependencies") or {}
    # Poetry always lists `python` itself; only meaningful if there's
    # at least one real package beyond that.
    return any(k for k in poetry_deps if k != "python")


def _pyproject_module_name(pyproject: Path, root: Path) -> str | None:
    """Best-effort import name for the smoke ``python -c "import X"``.

    Priority:
      1. ``app.py`` at workspace root → ``app`` (Heartline pattern)
      2. ``[project].name`` (PEP 621)
      3. ``[tool.poetry].name``
      4. None → smoke import is skipped (returns ``passed`` after
         install succeeds; install failure still catches dep drift)
    """
    if (root / "app.py").is_file():
        return "app"
    try:
        data = tomllib.loads(pyproject.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return None
    name = (data.get("project", {}) or {}).get("name")
    if isinstance(name, str) and name:
        return name.replace("-", "_")
    name = (data.get("tool", {}) or {}).get("poetry", {}).get("name")
    if isinstance(name, str) and name:
        return name.replace("-", "_")
    return None


# ──────────────────────────── aspect runners ──────────────────────────


_VENV_PYTHON_TOKEN = "<venv_python>"


def _resolve_command(cmd: Sequence[str], venv_python: Path | None) -> tuple[str, ...]:
    """Substitute the ``<venv_python>`` placeholder with the verifier
    venv's interpreter. Falls back to ``sys.executable`` only when no
    venv was provided — a defensive path; callers mark the aspect
    ``error`` upstream when the venv build failed, so this fallback
    should not decide a real verdict."""
    target = str(venv_python) if venv_python is not None else sys.executable
    return tuple(target if part == _VENV_PYTHON_TOKEN else part for part in cmd)


def _needs_aspect_venv(specs: Sequence[AspectSpec]) -> bool:
    """True when any aspect command references the ``<venv_python>``
    token — i.e. a Python pytest/ruff aspect that must run inside an
    isolated venv carrying the verifier toolchain."""
    return any(_VENV_PYTHON_TOKEN in cmd for spec in specs for cmd in spec.commands)


async def _build_aspect_venv(root: Path, tmpdir: Path) -> tuple[Path | None, str | None]:
    """Build a venv with the workspace installed + the verifier
    toolchain (pytest, ruff). Returns ``(venv_python, error)`` — on
    failure ``venv_python`` is None and ``error`` carries the reason
    (the caller marks code_test/code_lint as ``error``, not ``failed``,
    so an infra hiccup doesn't count against the model)."""
    venv_dir = tmpdir / ".venv"
    bin_dir = venv_dir / ("Scripts" if sys.platform == "win32" else "bin")
    python = bin_dir / "python"
    pip = bin_dir / "pip"

    exit_code, output = await _run_command((sys.executable, "-m", "venv", str(venv_dir)), cwd=root, timeout_s=60)
    if exit_code != 0:
        return None, f"verifier venv create failed (exit {exit_code})\n{output}"

    # Install the workspace itself when it's a package, so pytest sees
    # the workspace's declared deps and ``import <pkg>`` resolves.
    if (root / "pyproject.toml").exists():
        exit_code, output = await _run_command((str(pip), "install", "--quiet", "-e", "."), cwd=root, timeout_s=300)
        if exit_code != 0:
            return None, f"verifier venv workspace install failed (exit {exit_code})\n{output}"

    # The verifier toolchain — installed unconditionally so code_test /
    # code_lint can always run regardless of what the workspace declared
    # (a workspace pyproject may list ruff under [dev] or not at all).
    exit_code, output = await _run_command(
        (str(pip), "install", "--quiet", "pytest", "pytest-asyncio", "ruff", "httpx"),
        cwd=root,
        timeout_s=300,
    )
    if exit_code != 0:
        return None, f"verifier toolchain install failed (exit {exit_code})\n{output}"

    return python, None


@contextlib.asynccontextmanager
async def _aspect_venv(root: Path, specs: Sequence[AspectSpec]) -> AsyncIterator[tuple[Path | None, str | None]]:
    """Yield ``(venv_python, error)`` for the duration of an aspect run.

    Builds the venv once when any Python pytest/ruff aspect is present;
    yields ``(None, None)`` when no venv is needed. The tmp tree is
    always cleaned up. ``error`` is set (and ``venv_python`` None) when
    the build failed."""
    if not _needs_aspect_venv(specs):
        yield None, None
        return
    tmpdir = Path(tempfile.mkdtemp(prefix="bsnexus-aspect-venv-"))
    try:
        venv_python, error = await _build_aspect_venv(root, tmpdir)
        yield venv_python, error
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


async def _run_default_aspect(
    spec: AspectSpec, workspace_root: Path, venv_python: Path | None = None
) -> tuple[ProofAspectStatus, str, int | None]:
    """Run each command in order. First non-zero exit → failed.

    ``<venv_python>`` placeholders are substituted with the verifier
    venv interpreter."""
    last_exit: int | None = 0
    for cmd in spec.commands:
        resolved = _resolve_command(cmd, venv_python)
        exit_code, output = await _run_command(resolved, cwd=workspace_root, timeout_s=spec.timeout_s)
        last_exit = exit_code
        if exit_code != 0:
            return (
                ProofAspectStatus.failed,
                f"`{shlex.join(resolved)}` (exit {exit_code})\n{output}",
                exit_code,
            )
    cmd_summary = " && ".join(shlex.join(_resolve_command(cmd, venv_python)) for cmd in spec.commands)
    return ProofAspectStatus.passed, f"{cmd_summary} (exit 0)", last_exit


async def _run_install_smoke(
    spec: AspectSpec, workspace_root: Path, venv_python: Path | None = None
) -> tuple[ProofAspectStatus, str, int | None]:
    """Fresh venv + ``pip install -e .`` + ``python -c "import X"``.

    Builds its OWN venv on purpose — a clean-install smoke test must not
    reuse the shared aspect venv. ``venv_python`` is accepted for runner
    signature uniformity and ignored.

    Catches the heartline-style "import works in workspace because the
    workspace's interpreter already has the dep, but pyproject didn't
    declare it" class of bug. Costs ~30-90s; cleans up the tmp venv
    afterwards regardless of outcome."""
    pyproject = workspace_root / "pyproject.toml"
    if not pyproject.exists():
        return ProofAspectStatus.skipped, "pyproject.toml missing", None
    module = _pyproject_module_name(pyproject, workspace_root)

    tmpdir = Path(tempfile.mkdtemp(prefix="bsnexus-install-smoke-"))
    venv_dir = tmpdir / ".venv"
    bin_dir = venv_dir / ("Scripts" if sys.platform == "win32" else "bin")
    pip = bin_dir / "pip"
    python = bin_dir / "python"
    try:
        exit_code, output = await _run_command(
            (sys.executable, "-m", "venv", str(venv_dir)),
            cwd=workspace_root,
            timeout_s=60,
        )
        if exit_code != 0:
            return (
                ProofAspectStatus.error,
                f"venv create failed (exit {exit_code})\n{output}",
                exit_code,
            )

        exit_code, output = await _run_command(
            (str(pip), "install", "--quiet", "-e", "."),
            cwd=workspace_root,
            timeout_s=spec.timeout_s,
        )
        if exit_code != 0:
            return (
                ProofAspectStatus.failed,
                f"`pip install -e .` failed (exit {exit_code})\n{output}",
                exit_code,
            )

        if module is None:
            return (
                ProofAspectStatus.passed,
                "pip install -e . succeeded (no module name resolved for import smoke)",
                0,
            )

        exit_code, output = await _run_command(
            (str(python), "-c", f"import {module}"),
            cwd=workspace_root,
            timeout_s=30,
        )
        if exit_code != 0:
            return (
                ProofAspectStatus.failed,
                f"`python -c 'import {module}'` failed (exit {exit_code}) — "
                f"declared deps in pyproject likely miss runtime imports\n{output}",
                exit_code,
            )
        return ProofAspectStatus.passed, f"venv + install + import {module} (exit 0)", 0
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


async def _run_docker_build(
    spec: AspectSpec, workspace_root: Path, venv_python: Path | None = None
) -> tuple[ProofAspectStatus, str, int | None]:
    """``docker build --quiet`` against the workspace's Dockerfile.

    ``venv_python`` is accepted for runner signature uniformity and
    ignored — docker build needs no Python venv.

    Catches the class of Dockerfile bug that pytest/lint/install-smoke
    can't see: deprecated CLI invocations (``poetry export`` removed
    from recent Poetry), exec-form shell-expansion traps (``$PORT``
    in ``CMD [...]``), missing files in ``COPY``, etc.

    Skips gracefully when docker isn't available — verifier
    environments without a docker socket shouldn't fail every code
    deliverable. Image is tagged with a unique throwaway name and
    cleaned up on the way out."""
    if shutil.which("docker") is None:
        return (
            ProofAspectStatus.skipped,
            "docker CLI not available in this verifier environment",
            None,
        )
    tag = f"bsnexus-smoke-{uuid.uuid4().hex[:12]}"
    try:
        exit_code, output = await _run_command(
            ("docker", "build", "--quiet", "--tag", tag, "."),
            cwd=workspace_root,
            timeout_s=spec.timeout_s,
        )
        if exit_code != 0:
            return (
                ProofAspectStatus.failed,
                f"`docker build` failed (exit {exit_code})\n{output}",
                exit_code,
            )
        return ProofAspectStatus.passed, f"docker build (exit 0) — image {tag[:24]}…", 0
    finally:
        # Best-effort cleanup. If docker isn't reachable here we're
        # already out of the aspect's verdict path; swallow the error.
        try:
            await _run_command(("docker", "rmi", "--force", tag), cwd=workspace_root, timeout_s=30)
        except Exception:  # noqa: BLE001
            pass


_RUNNERS: dict[ProofAspectType, Any] = {
    ProofAspectType.code_test: _run_default_aspect,
    ProofAspectType.code_lint: _run_default_aspect,
    ProofAspectType.code_install_smoke: _run_install_smoke,
    ProofAspectType.code_build: _run_docker_build,
}


async def _run_one_aspect(
    *,
    spec: AspectSpec,
    root: Path,
    venv_python: Path | None,
    venv_error: str | None,
    deliverable_id: str | None = None,
) -> tuple[ProofAspectStatus, str, int | None]:
    """Dispatch one aspect to its runner.

    When the aspect needs the verifier venv but the venv build failed,
    the aspect is ``error`` (infra failure → human_review_required in
    roll-up), NOT ``failed`` — a broken verifier env must not count
    against the model's code."""
    needs_venv = _VENV_PYTHON_TOKEN in {part for cmd in spec.commands for part in cmd}
    if needs_venv and venv_python is None:
        return (
            ProofAspectStatus.error,
            venv_error or "verifier venv unavailable",
            None,
        )
    runner = _RUNNERS.get(spec.aspect_type, _run_default_aspect)
    try:
        return await runner(spec, root, venv_python)
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "verification_aspect_runner_crashed",
            aspect_type=spec.aspect_type.value,
            deliverable_id=deliverable_id,
            error=str(exc),
        )
        return (
            ProofAspectStatus.error,
            f"runner crashed: {exc.__class__.__name__}: {exc}",
            None,
        )


# ──────────────────────────── subprocess helper ──────────────────────────


async def _run_command(command: Sequence[str], *, cwd: Path, timeout_s: int) -> tuple[int | None, str]:
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        return 127, str(exc)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
    except (asyncio.TimeoutError, subprocess.TimeoutExpired):
        process.kill()
        stdout, stderr = await process.communicate()
        out = _tail(stdout, stderr)
        return None, f"{out}\nTimed out after {timeout_s}s"
    return process.returncode, _tail(stdout, stderr)


def _tail(stdout: bytes, stderr: bytes) -> str:
    parts = []
    if stdout:
        parts.append(stdout.decode("utf-8", errors="replace"))
    if stderr:
        parts.append(stderr.decode("utf-8", errors="replace"))
    out = "\n".join(parts)
    return out[-4000:]


SETUP_ONLY_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("pip", "install"),
    ("python", "-m", "pip", "install"),
    ("python3", "-m", "pip", "install"),
    ("uv", "sync"),
    ("uv", "pip", "install"),
    ("npm", "install"),
    ("npm", "ci"),
    ("pnpm", "install"),
    ("yarn", "install"),
    ("corepack", "enable"),
)


def is_setup_only_command(command: str | Sequence[str]) -> bool:
    """A command whose only effect is environment setup (no verifier
    semantics). The M0 benchmark task validator rejects these as
    'verifier_command' candidates so a task can't claim "pip install"
    as its proof of success."""
    parts = tuple(shlex.split(command) if isinstance(command, str) else command)
    normalized = tuple(part.strip() for part in parts if part.strip())
    return any(normalized[: len(prefix)] == prefix for prefix in SETUP_ONLY_COMMANDS)


@dataclass(frozen=True)
class AspectProbeResult:
    """Aspect outcome from a dry-run probe — no DB persistence, no
    ``VerificationAspect`` row created. Used by the dispatcher to inject
    aspect-failure feedback back into the work-phase loop so the model
    can self-correct before the verifier worker stamps the final proof.
    """

    aspect_type: ProofAspectType
    status: ProofAspectStatus
    summary: str
    exit_code: int | None
    blocking: bool


async def probe_aspects(
    *,
    workspace_root: Path | str,
    deliverable_type: DeliverableType,
    changed_files: Sequence[str] = (),
) -> list[AspectProbeResult]:
    """Run every applicable aspect against the workspace without
    persisting anything. Returns the per-aspect results so the caller
    can decide to inject feedback / retry the work phase. The actual
    verifier-worker run later re-runs the same aspects and persists
    them; this probe is idempotent so the re-run is cheap when all
    aspects already pass."""
    root = Path(workspace_root)
    specs = select_verification_aspects(
        workspace_root=root,
        deliverable_type=deliverable_type,
        changed_files=changed_files,
    )
    results: list[AspectProbeResult] = []
    async with _aspect_venv(root, specs) as (venv_python, venv_error):
        for spec in specs:
            status, summary, exit_code = await _run_one_aspect(
                spec=spec,
                root=root,
                venv_python=venv_python,
                venv_error=venv_error,
            )
            results.append(
                AspectProbeResult(
                    aspect_type=spec.aspect_type,
                    status=status,
                    summary=summary,
                    exit_code=exit_code,
                    blocking=spec.blocking,
                )
            )
    return results


__all__ = [
    "AspectProbeResult",
    "AspectSpec",
    "SETUP_ONLY_COMMANDS",
    "aspects_for_deliverable",
    "is_setup_only_command",
    "latest_aspect_of_type",
    "probe_aspects",
    "rollup_proof_state",
    "run_verification",
    "select_verification_aspects",
]


# silence unused-import warnings on stdlib bits that are needed but not
# directly referenced above when type checkers stub them out.
_ = (json, uuid)
