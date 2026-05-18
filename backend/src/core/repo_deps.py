"""Cloned-repo dependency install for the verification sandbox (G-E).

A ``github_connected`` project's cloned repo carries a real dependency
tree (``httpx``, ``fastapi``, … via ``uv``; or a ``node_modules`` tree
via ``pnpm``). The Part B work sandbox ships only a generic toolchain,
so the work LLM's declared verification contract — e.g. ``pytest`` —
fails at *collection* with ``ModuleNotFoundError`` when those deps were
never installed.

:func:`ensure_repo_dependencies` runs the repo's install step inside
the sandbox once, before the verification aspects run. Best-effort and
non-fatal: a failed/absent install is reported via :class:`InstallResult`
and logged — it never raises, because a genuine dependency problem
should surface as a failed aspect with a clear message, not as a
verifier crash.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import structlog

from backend.src.core.sandbox import SandboxSession

logger = structlog.get_logger(__name__)

# Dependency installs pull from the network and can be slow on a cold
# cache — generous, but bounded so a hung registry can't wedge the
# verifier.
DEP_INSTALL_TIMEOUT_S = 300


@dataclass(frozen=True)
class InstallResult:
    """Outcome of :func:`ensure_repo_dependencies`.

    ``status`` is one of:
      - ``skipped``   — no recognised dependency manifest in the repo.
      - ``installed`` — the install command ran and exited 0.
      - ``failed``    — the install command ran non-zero, or errored.
    """

    status: str
    detail: str | None


def _detect_install_command(root: Path) -> str | None:
    """Return the install command for whatever manifest the repo root
    carries, or ``None`` when none is recognised. Lockfiles win over
    bare manifests so the install is reproducible."""
    if (root / "uv.lock").is_file() or (root / "pyproject.toml").is_file():
        return "uv sync"
    if (root / "pnpm-lock.yaml").is_file():
        return "pnpm install --frozen-lockfile"
    if (root / "package-lock.json").is_file():
        return "npm ci"
    if (root / "yarn.lock").is_file():
        return "yarn install --frozen-lockfile"
    if (root / "package.json").is_file():
        return "npm install"
    return None


async def ensure_repo_dependencies(
    *,
    root: Path | str,
    sandbox_session: SandboxSession,
) -> InstallResult:
    """Install the cloned repo's dependencies inside ``sandbox_session``.

    No-op (``skipped``) when the repo carries no recognised manifest.
    Best-effort — never raises; a failure is logged and returned so the
    caller can proceed (the verification aspect will then fail with a
    concrete error rather than the verifier crashing)."""
    root = Path(root)
    command = _detect_install_command(root)
    if command is None:
        return InstallResult(status="skipped", detail="no dependency manifest")

    try:
        result = await sandbox_session.exec(command, timeout_s=DEP_INSTALL_TIMEOUT_S, shell=True)
    except Exception as exc:  # noqa: BLE001 — best-effort, never abort verification
        logger.warning("repo_deps_install_errored", command=command, error=str(exc))
        return InstallResult(status="failed", detail=str(exc))

    if result.timed_out:
        logger.warning("repo_deps_install_timed_out", command=command)
        return InstallResult(status="failed", detail=f"{command} timed out")
    if result.exit_code != 0:
        detail = (result.stderr or result.stdout or "").strip()[:500]
        logger.warning("repo_deps_install_failed", command=command, exit_code=result.exit_code, detail=detail)
        return InstallResult(status="failed", detail=f"{command} exited {result.exit_code}: {detail}")

    logger.info("repo_deps_installed", command=command)
    return InstallResult(status="installed", detail=command)
