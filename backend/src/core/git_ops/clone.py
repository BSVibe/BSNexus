"""github_connected workspace clone (Phase 3).

For a ``github_connected`` project the work LLM must see the real repo
contents — not an empty workspace. :func:`ensure_repo_cloned` does a
shallow, single-branch clone of the bound repo into the project
workspace before the work phase runs.

Read-context only: deliverable commits/PRs still land via the GitHub
Contents API (``core/git_ops/commit.py`` + ``pull_request.py``), so this
local clone is never pushed. The ``.git`` directory it leaves behind is
filtered out of the worker's file context by ``project_workspace``.

Soft-fail posture: a clone failure logs loudly and leaves the workspace
as provisioned (empty + the seeded ``AGENTS.md``) rather than aborting
the run — the same non-fatal stance the commit/PR steps take. The
verification contract is what catches work produced against a missing
context.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

import structlog

from backend.src.config import settings as app_settings
from backend.src.core.encryption import EncryptionManager
from backend.src.models import Project
from backend.src.models.project import WorkspaceType

logger = structlog.get_logger(__name__)

# (returncode, stdout, stderr). Injected for tests; the default runs git.
CloneRunner = Callable[..., Awaitable[tuple[int, str, str]]]

# A clone failure is non-fatal; the work phase still runs. Keep the
# subprocess from hanging the orchestrator if the remote stalls.
_CLONE_TIMEOUT_S = 180


@dataclass(frozen=True)
class CloneResult:
    """Outcome of an :func:`ensure_repo_cloned` call.

    ``status`` is one of:
      - ``skipped``          — not a github_connected project / no repo bound.
      - ``already_present``  — workspace already carries a ``.git`` dir.
      - ``cloned``           — repo freshly cloned into the workspace.
      - ``failed``           — clone attempted and failed (soft-fail).
    """

    status: str
    detail: str | None


def _authenticated_clone_url(repo_url: str, token: str) -> str:
    """Inject the PAT into an ``https://github.com`` URL so the clone can
    authenticate. Non-https URLs (e.g. ``file://`` test remotes) and the
    no-token case pass through unchanged."""
    if token and repo_url.startswith("https://"):
        return "https://x-access-token:" + token + "@" + repo_url[len("https://") :]
    return repo_url


def _redact(text: str, token: str) -> str:
    """Mask the PAT anywhere it appears in subprocess output before it
    reaches a log line or a stored ``detail``."""
    if not token:
        return text
    return text.replace(token, "***")


async def _run_git(argv: list[str], *, cwd: str | None = None) -> tuple[int, str, str]:
    """Default clone runner — spawn ``git`` and capture its output."""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=_CLONE_TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "", f"git clone timed out after {_CLONE_TIMEOUT_S}s"
    return proc.returncode or 0, stdout_b.decode(errors="replace"), stderr_b.decode(errors="replace")


def _merge_into(src: Path, dest: Path) -> None:
    """Move every entry from ``src`` into ``dest``. The repo's copy wins
    over anything already in ``dest`` (e.g. the seeded ``AGENTS.md``)."""
    for entry in src.iterdir():
        target = dest / entry.name
        if target.exists():
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
        shutil.move(str(entry), str(target))


async def ensure_repo_cloned(
    *,
    project: Project,
    workspace_dir: Path,
    clone_runner: CloneRunner | None = None,
    decrypt: Callable[[str], str] | None = None,
) -> CloneResult:
    """Clone ``project``'s bound repo into ``workspace_dir`` when needed.

    No-op unless ``project`` is ``github_connected`` with a repo bound.
    Idempotent — a workspace already carrying ``.git`` is left untouched
    so the loop policy's clean re-runs don't re-clone. Soft-fails: a
    clone error is logged and reported via :class:`CloneResult`, never
    raised.
    """
    if project.workspace_type != WorkspaceType.github_connected:
        return CloneResult(status="skipped", detail="not a github_connected project")
    if not project.github_repo_url:
        logger.warning("clone_skipped_no_repo", project_id=str(project.id))
        return CloneResult(status="skipped", detail="no repo bound")

    workspace_dir = Path(workspace_dir)
    if (workspace_dir / ".git").is_dir():
        return CloneResult(status="already_present", detail=None)

    runner = clone_runner or _run_git
    branch = (project.github_branch or "main").strip() or "main"

    token = ""
    if project.github_token_encrypted:
        decryptor = decrypt or EncryptionManager(app_settings.encryption_key).decrypt_value
        token = decryptor(project.github_token_encrypted)

    clone_url = _authenticated_clone_url(project.github_repo_url, token)
    # Clone into a sibling temp dir, then merge in — ``git clone``
    # refuses a non-empty destination, and the workspace already holds
    # the seeded ``AGENTS.md``.
    temp_dir = workspace_dir.parent / f".clone-{uuid.uuid4().hex}"
    argv = [
        "git",
        "clone",
        "--depth",
        "1",
        "--single-branch",
        "--branch",
        branch,
        clone_url,
        str(temp_dir),
    ]

    try:
        returncode, _stdout, stderr = await runner(argv)
        if returncode != 0:
            detail = _redact(stderr.strip() or f"git clone exited {returncode}", token)
            logger.warning(
                "clone_failed",
                project_id=str(project.id),
                repo_url=project.github_repo_url,
                branch=branch,
                returncode=returncode,
                detail=detail,
            )
            return CloneResult(status="failed", detail=detail)

        _merge_into(temp_dir, workspace_dir)
        logger.info(
            "repo_cloned",
            project_id=str(project.id),
            repo_url=project.github_repo_url,
            branch=branch,
            workspace_dir=str(workspace_dir),
        )
        return CloneResult(status="cloned", detail=None)
    except Exception as exc:  # noqa: BLE001 — soft-fail, never abort the run
        detail = _redact(str(exc), token)
        logger.warning(
            "clone_failed_unexpected",
            project_id=str(project.id),
            repo_url=project.github_repo_url,
            detail=detail,
        )
        return CloneResult(status="failed", detail=detail)
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
