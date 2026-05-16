"""Multi-aspect verifier tests.

Covers:
  - ``select_verification_aspects`` activation rules per aspect_type
  - ``run_verification`` end-to-end against a real tmp workspace
  - ``rollup_proof_state`` semantics across aspect status combinations
  - ``is_setup_only_command`` (M0 helper still exposed)
"""

from __future__ import annotations

import sys
import uuid

import pytest
from sqlalchemy import select

from backend.src.core.domain import (
    DeliverableStatus,
    DeliverableType,
    ProofAspectStatus,
    ProofAspectType,
    ProofState,
    RequestStatus,
)
from backend.src.core.verification import (
    is_setup_only_command,
    rollup_proof_state,
    run_verification,
    select_verification_aspects,
)
from backend.src.core.work_steps import GreenfieldStateError, transition_request
from backend.src.models import Deliverable, Project, Request, VerificationAspect


async def _make_deliverable(
    db_session,
    tenant_id: uuid.UUID,
    *,
    deliverable_type: DeliverableType = DeliverableType.code,
) -> tuple[Request, Deliverable]:
    project = Project(tenant_id=tenant_id, name="Verifier", description="")
    db_session.add(project)
    await db_session.flush()
    request = Request(tenant_id=tenant_id, project_id=project.id, intent="Add /healthz")
    db_session.add(request)
    await db_session.flush()
    deliverable = Deliverable(
        tenant_id=tenant_id,
        project_id=project.id,
        request_id=request.id,
        type=deliverable_type,
        title="Implementation",
        artifact_refs=[{"path": "src/app.py"}],
        status=DeliverableStatus.draft,
        proof_state=ProofState.verification_missing,
    )
    db_session.add(deliverable)
    await db_session.commit()
    await db_session.refresh(deliverable)
    await db_session.refresh(request)
    return request, deliverable


# ────────────────────────────── selection ──────────────────────────────


def test_selects_python_code_test_aspect_from_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n")
    (tmp_path / "tests").mkdir()

    specs = select_verification_aspects(
        workspace_root=tmp_path,
        deliverable_type=DeliverableType.code,
        changed_files=["src/app.py"],
    )

    types = [s.aspect_type for s in specs]
    assert ProofAspectType.code_test in types
    test_spec = next(s for s in specs if s.aspect_type == ProofAspectType.code_test)
    # The verifier runs pytest inside an isolated venv (``<venv_python>``
    # substituted at run time) — the prod interpreter has no pytest.
    assert test_spec.commands == (("<venv_python>", "-m", "pytest"),)


def test_selects_python_code_test_from_root_level_test_file(tmp_path):
    """Bare workspace with a root-level ``test_*.py`` still activates
    code_test — pytest discovers root-level files fine."""
    (tmp_path / "calculator.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "test_calculator.py").write_text(
        "from calculator import add\ndef test_add():\n    assert add(1, 2) == 3\n"
    )

    specs = select_verification_aspects(
        workspace_root=tmp_path,
        deliverable_type=DeliverableType.code,
        changed_files=["calculator.py", "test_calculator.py"],
    )
    types = [s.aspect_type for s in specs]
    assert ProofAspectType.code_test in types


def test_selects_node_code_test_from_package_json(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"test": "vitest run"}}')
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")

    specs = select_verification_aspects(
        workspace_root=tmp_path,
        deliverable_type=DeliverableType.code,
        changed_files=["src/App.tsx"],
    )
    test_spec = next(s for s in specs if s.aspect_type == ProofAspectType.code_test)
    assert test_spec.commands == (("pnpm", "test"),)
    assert "package.json" in test_spec.required_refs


def test_selects_node_build_when_test_script_default(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"build": "vite build"}}')

    specs = select_verification_aspects(
        workspace_root=tmp_path,
        deliverable_type=DeliverableType.preview,
        changed_files=["package.json"],
    )
    test_spec = next(s for s in specs if s.aspect_type == ProofAspectType.code_test)
    assert test_spec.commands == (("npm", "run", "build"),)


def test_no_aspects_for_docs_only_deliverable(tmp_path):
    (tmp_path / "README.md").write_text("# docs only\n")

    specs = select_verification_aspects(
        workspace_root=tmp_path,
        deliverable_type=DeliverableType.doc,
        changed_files=["README.md"],
    )
    assert specs == []


def test_no_aspects_for_lone_python_file_without_test(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n")

    specs = select_verification_aspects(
        workspace_root=tmp_path,
        deliverable_type=DeliverableType.code,
        changed_files=["calc.py"],
    )
    # No pyproject, no tests dir, no test file → no aspects.
    assert specs == []


def test_lint_aspect_activates_when_ruff_declared(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\n[project.optional-dependencies]\ndev = ['ruff>=0.5.0']\n"
    )
    (tmp_path / "tests").mkdir()

    specs = select_verification_aspects(
        workspace_root=tmp_path,
        deliverable_type=DeliverableType.code,
        changed_files=["app.py"],
    )
    types = [s.aspect_type for s in specs]
    assert ProofAspectType.code_lint in types
    lint = next(s for s in specs if s.aspect_type == ProofAspectType.code_lint)
    assert lint.commands[0][:3] == ("<venv_python>", "-m", "ruff")


def test_lint_aspect_skipped_when_ruff_not_declared(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\ndependencies=['fastapi']\n")
    (tmp_path / "tests").mkdir()

    specs = select_verification_aspects(
        workspace_root=tmp_path,
        deliverable_type=DeliverableType.code,
        changed_files=["app.py"],
    )
    types = [s.aspect_type for s in specs]
    assert ProofAspectType.code_lint not in types


def test_install_smoke_aspect_activates_with_pyproject_deps(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\ndependencies=['fastapi>=0.110.0']\n")
    specs = select_verification_aspects(
        workspace_root=tmp_path,
        deliverable_type=DeliverableType.code,
        changed_files=["app.py"],
    )
    types = [s.aspect_type for s in specs]
    assert ProofAspectType.code_install_smoke in types


def test_install_smoke_aspect_absent_when_no_deps_declared(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    specs = select_verification_aspects(
        workspace_root=tmp_path,
        deliverable_type=DeliverableType.code,
        changed_files=["app.py"],
    )
    types = [s.aspect_type for s in specs]
    assert ProofAspectType.code_install_smoke not in types


def test_code_build_aspect_activates_when_dockerfile_present(tmp_path):
    """Top-level Dockerfile → code_build aspect added."""
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")
    specs = select_verification_aspects(
        workspace_root=tmp_path,
        deliverable_type=DeliverableType.code,
        changed_files=["Dockerfile"],
    )
    types = [s.aspect_type for s in specs]
    assert ProofAspectType.code_build in types


def test_code_build_aspect_absent_without_dockerfile(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\ndependencies=['fastapi']\n")
    specs = select_verification_aspects(
        workspace_root=tmp_path,
        deliverable_type=DeliverableType.code,
        changed_files=["app.py"],
    )
    types = [s.aspect_type for s in specs]
    assert ProofAspectType.code_build not in types


# ─────────────────────────── setup-only helper ───────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        "pip install -r requirements.txt",
        ["python", "-m", "pip", "install", "-r", "requirements.txt"],
        ["uv", "sync"],
        ["npm", "install"],
        ["pnpm", "install"],
    ],
)
def test_setup_only_commands_helper(command):
    assert is_setup_only_command(command)


# ─────────────────────────── roll-up semantics ───────────────────────────


class _FakeAspect:
    """In-memory stand-in so roll-up logic can be unit-tested without
    flushing rows to the test DB."""

    def __init__(self, status: ProofAspectStatus, blocking: bool = True) -> None:
        self.status = status
        self.blocking = blocking


def test_rollup_no_aspects_falls_to_human_review():
    assert rollup_proof_state([]) == ProofState.human_review_required


def test_rollup_all_passed_blocking_verifies():
    aspects = [_FakeAspect(ProofAspectStatus.passed), _FakeAspect(ProofAspectStatus.passed)]
    assert rollup_proof_state(aspects) == ProofState.verified  # type: ignore[arg-type]


def test_rollup_any_failed_fails():
    aspects = [_FakeAspect(ProofAspectStatus.passed), _FakeAspect(ProofAspectStatus.failed)]
    assert rollup_proof_state(aspects) == ProofState.verification_failed  # type: ignore[arg-type]


def test_rollup_any_error_falls_to_human_review():
    """An infra error in one aspect shouldn't count as a model failure."""
    aspects = [_FakeAspect(ProofAspectStatus.passed), _FakeAspect(ProofAspectStatus.error)]
    assert rollup_proof_state(aspects) == ProofState.human_review_required  # type: ignore[arg-type]


def test_rollup_still_running_marks_verifying():
    aspects = [_FakeAspect(ProofAspectStatus.passed), _FakeAspect(ProofAspectStatus.running)]
    assert rollup_proof_state(aspects) == ProofState.verifying  # type: ignore[arg-type]


def test_rollup_non_blocking_failures_do_not_block_verified():
    aspects = [
        _FakeAspect(ProofAspectStatus.passed),
        _FakeAspect(ProofAspectStatus.failed, blocking=False),
    ]
    assert rollup_proof_state(aspects) == ProofState.verified  # type: ignore[arg-type]


# ─────────────────────────── end-to-end runs ───────────────────────────


@pytest.mark.asyncio
async def test_run_verification_with_passing_pytest_marks_verified(
    tmp_path, db_session, mock_tenant_id, seeded_tenant, fake_aspect_venv
):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    (tmp_path / "test_demo.py").write_text("def test_truth():\n    assert True\n")
    _, deliverable = await _make_deliverable(db_session, mock_tenant_id)

    await run_verification(
        deliverable=deliverable,
        workspace_root=tmp_path,
        changed_files=["test_demo.py"],
        session=db_session,
    )

    await db_session.refresh(deliverable)
    assert deliverable.proof_state == ProofState.verified
    aspects = (
        (
            await db_session.execute(
                select(VerificationAspect).where(VerificationAspect.deliverable_id == deliverable.id)
            )
        )
        .scalars()
        .all()
    )
    test_aspect = next(a for a in aspects if a.aspect_type == ProofAspectType.code_test)
    assert test_aspect.status == ProofAspectStatus.passed
    assert test_aspect.exit_code == 0


@pytest.mark.asyncio
async def test_run_verification_with_failing_pytest_marks_failed_and_blocks_ship(
    tmp_path, db_session, mock_tenant_id, seeded_tenant, fake_aspect_venv
):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    (tmp_path / "test_demo.py").write_text("def test_no():\n    assert False, 'intentional'\n")
    request, deliverable = await _make_deliverable(db_session, mock_tenant_id)
    await transition_request(request=request, target=RequestStatus.running, session=db_session)
    await transition_request(request=request, target=RequestStatus.review_ready, session=db_session)

    await run_verification(
        deliverable=deliverable,
        workspace_root=tmp_path,
        changed_files=["test_demo.py"],
        session=db_session,
    )

    await db_session.refresh(deliverable)
    assert deliverable.proof_state == ProofState.verification_failed
    with pytest.raises(GreenfieldStateError):
        await transition_request(request=request, target=RequestStatus.shipped, session=db_session)


@pytest.mark.asyncio
async def test_run_verification_with_no_applicable_aspects_human_review(
    tmp_path, db_session, mock_tenant_id, seeded_tenant
):
    (tmp_path / "README.md").write_text("# docs only\n")
    _, deliverable = await _make_deliverable(db_session, mock_tenant_id, deliverable_type=DeliverableType.doc)

    await run_verification(
        deliverable=deliverable,
        workspace_root=tmp_path,
        changed_files=["README.md"],
        session=db_session,
    )

    await db_session.refresh(deliverable)
    assert deliverable.proof_state == ProofState.human_review_required
    aspects = (
        (
            await db_session.execute(
                select(VerificationAspect).where(VerificationAspect.deliverable_id == deliverable.id)
            )
        )
        .scalars()
        .all()
    )
    assert aspects == []


# ──────────────── install_smoke module derivation (Cycle 11) ─────────────


def test_module_name_root_app_py(tmp_path):
    from backend.src.core.verification import _pyproject_module_name

    (tmp_path / "app.py").write_text("x = 1\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='whatever'\n")
    assert _pyproject_module_name(tmp_path / "pyproject.toml", tmp_path) == "app"


def test_module_name_src_layout_loose_entry_module(tmp_path):
    """Cycle 11 dogfood case: Direction said "a single src/app.py is
    fine" → src/app.py + src/__init__.py. Import target must be
    ``src.app``, NOT the project name."""
    from backend.src.core.verification import _pyproject_module_name

    src = tmp_path / "src"
    src.mkdir()
    (src / "__init__.py").write_text("")
    (src / "app.py").write_text("x = 1\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='task-tracker'\n")
    assert _pyproject_module_name(tmp_path / "pyproject.toml", tmp_path) == "src.app"


def test_module_name_src_nested_package(tmp_path):
    from backend.src.core.verification import _pyproject_module_name

    pkg = tmp_path / "src" / "task_tracker"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='task-tracker'\n")
    assert _pyproject_module_name(tmp_path / "pyproject.toml", tmp_path) == "task_tracker"


def test_module_name_falls_back_to_project_name(tmp_path):
    """No filesystem signal → declared project name (normalized)."""
    from backend.src.core.verification import _pyproject_module_name

    (tmp_path / "pyproject.toml").write_text("[project]\nname='my-pkg'\n")
    assert _pyproject_module_name(tmp_path / "pyproject.toml", tmp_path) == "my_pkg"


# ─────────────────── install_smoke diagnostic (Cycle 11) ─────────────────


def test_install_smoke_hint_packaging_when_package_not_importable():
    """The package itself missing → packaging-config diagnosis, so the
    aspect-feedback retry loop can fix pyproject without the universal
    prompt carrying stack-specific packaging law."""
    from backend.src.core.verification import _install_smoke_hint

    hint = _install_smoke_hint("task_tracker", "ModuleNotFoundError: No module named 'task_tracker'")
    assert "packaging config" in hint.lower()
    assert "pyproject" in hint.lower()


def test_install_smoke_hint_missing_dep_when_other_module_missing():
    """A *different* module missing → missing runtime dependency."""
    from backend.src.core.verification import _install_smoke_hint

    hint = _install_smoke_hint("task_tracker", "ModuleNotFoundError: No module named 'requests'")
    assert "runtime dependency" in hint.lower()


# ───────────────────── verifier venv (Cycle 9 fix) ──────────────────────


def test_needs_aspect_venv_true_for_python_test_aspect(tmp_path):
    from backend.src.core.verification import _needs_aspect_venv

    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    (tmp_path / "tests").mkdir()
    specs = select_verification_aspects(workspace_root=tmp_path, deliverable_type=DeliverableType.code)
    assert _needs_aspect_venv(specs) is True


def test_needs_aspect_venv_false_for_node_only(tmp_path):
    """A pure Node workspace's code_test runs ``pnpm test`` — no Python
    venv needed, so no ``<venv_python>`` token, so no venv build."""
    from backend.src.core.verification import _needs_aspect_venv

    (tmp_path / "package.json").write_text('{"scripts": {"test": "vitest run"}}')
    specs = select_verification_aspects(workspace_root=tmp_path, deliverable_type=DeliverableType.code)
    assert _needs_aspect_venv(specs) is False


def test_resolve_command_substitutes_venv_python():
    from pathlib import Path

    from backend.src.core.verification import _resolve_command

    resolved = _resolve_command(("<venv_python>", "-m", "pytest"), Path("/tmp/v/bin/python"))
    assert resolved == ("/tmp/v/bin/python", "-m", "pytest")


def test_resolve_command_falls_back_to_sys_executable_when_no_venv():
    from backend.src.core.verification import _resolve_command

    resolved = _resolve_command(("<venv_python>", "-m", "ruff"), None)
    assert resolved == (sys.executable, "-m", "ruff")


@pytest.mark.asyncio
async def test_run_one_aspect_marks_error_when_venv_build_failed():
    """A failed verifier-venv build must surface as ``error`` (infra),
    NOT ``failed`` — a broken verifier env can't count against the
    model's code. ``error`` rolls up to human_review_required."""
    from pathlib import Path

    from backend.src.core.verification import AspectSpec, _run_one_aspect

    spec = AspectSpec(
        aspect_type=ProofAspectType.code_test,
        commands=(("<venv_python>", "-m", "pytest"),),
        required_refs=("tests/",),
        timeout_s=300,
        blocking=True,
    )
    status, summary, exit_code = await _run_one_aspect(
        spec=spec,
        root=Path("/tmp"),
        venv_python=None,
        venv_error="verifier toolchain install failed (exit 1)",
    )
    assert status == ProofAspectStatus.error
    assert "verifier toolchain install failed" in summary
    assert exit_code is None


@pytest.mark.asyncio
async def test_run_verification_venv_build_failure_is_human_review(
    tmp_path, db_session, mock_tenant_id, seeded_tenant, monkeypatch
):
    """When the verifier venv can't be built, code_test → error →
    deliverable rolls up to human_review_required (not verification_failed)."""
    import backend.src.core.verification as verif

    async def _failing_build(root, tmpdir):
        return None, "verifier venv create failed (exit 1)"

    monkeypatch.setattr(verif, "_build_aspect_venv", _failing_build)

    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    (tmp_path / "test_demo.py").write_text("def test_truth():\n    assert True\n")
    _, deliverable = await _make_deliverable(db_session, mock_tenant_id)

    await run_verification(
        deliverable=deliverable,
        workspace_root=tmp_path,
        changed_files=["test_demo.py"],
        session=db_session,
    )

    await db_session.refresh(deliverable)
    assert deliverable.proof_state == ProofState.human_review_required
