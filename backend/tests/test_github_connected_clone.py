"""Phase 3 — github_connected workspace clone tests.

``ensure_repo_cloned`` shallow-clones a bound GitHub repo into the
project workspace so the work LLM sees real repo contents. Read-context
only — deliverable commits/PRs still go via the Contents API, so the
local clone is never pushed.

The unit tests inject a fake ``clone_runner`` so no network is touched;
one integration test exercises a real ``git clone`` from a local
``file://`` repo (offline-safe).
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import pytest

from backend.src.core.git_ops.clone import (
    CloneResult,
    _authenticated_clone_url,
    _redact,
    ensure_repo_cloned,
)
from backend.src.models import Project
from backend.src.models.project import WorkspaceType


def _project(
    *,
    workspace_type: WorkspaceType,
    repo_url: str | None = None,
    branch: str | None = "main",
    token_encrypted: str | None = None,
    workspace_dir: str | None = None,
) -> Project:
    return Project(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        name="p",
        description="",
        workspace_type=workspace_type,
        workspace_dir=workspace_dir,
        github_repo_url=repo_url,
        github_branch=branch,
        github_token_encrypted=token_encrypted,
    )


# ───────────────────────────── url helpers ─────────────────────────────


def test_authenticated_clone_url_injects_token_for_https_github() -> None:
    url = _authenticated_clone_url("https://github.com/acme/widget.git", "ghp_secret")
    assert url == "https://x-access-token:ghp_secret@github.com/acme/widget.git"


def test_authenticated_clone_url_passes_through_without_token() -> None:
    assert _authenticated_clone_url("https://github.com/acme/widget.git", "") == "https://github.com/acme/widget.git"


def test_authenticated_clone_url_passes_through_non_https() -> None:
    # A file:// remote (used by the integration test) must not be mangled.
    assert _authenticated_clone_url("file:///tmp/repo", "ghp_secret") == "file:///tmp/repo"


def test_redact_masks_token_everywhere() -> None:
    text = "fatal: could not read from https://x-access-token:ghp_secret@github.com/x"
    masked = _redact(text, "ghp_secret")
    assert "ghp_secret" not in masked
    assert "***" in masked


def test_redact_is_noop_for_empty_token() -> None:
    assert _redact("no secret here", "") == "no secret here"


# ───────────────────────────── no-op paths ─────────────────────────────


@pytest.mark.asyncio
async def test_noop_for_non_github_connected(tmp_path) -> None:
    project = _project(workspace_type=WorkspaceType.server_managed)
    result = await ensure_repo_cloned(project=project, workspace_dir=tmp_path)
    assert result.status == "skipped"
    assert not (tmp_path / ".git").exists()


@pytest.mark.asyncio
async def test_noop_when_repo_not_bound(tmp_path) -> None:
    project = _project(workspace_type=WorkspaceType.github_connected, repo_url=None)
    result = await ensure_repo_cloned(project=project, workspace_dir=tmp_path)
    assert result.status == "skipped"


@pytest.mark.asyncio
async def test_idempotent_when_git_dir_present(tmp_path) -> None:
    """A workspace already carrying a ``.git`` dir is left untouched —
    the loop policy re-runs from scratch but the workspace persists."""
    (tmp_path / ".git").mkdir()
    project = _project(
        workspace_type=WorkspaceType.github_connected,
        repo_url="https://github.com/acme/widget.git",
    )
    ran: list[object] = []

    async def _runner(argv, *, cwd=None):  # pragma: no cover - must not run
        ran.append(argv)
        return 0, "", ""

    result = await ensure_repo_cloned(project=project, workspace_dir=tmp_path, clone_runner=_runner)
    assert result.status == "already_present"
    assert ran == []


# ───────────────────────────── clone via fake runner ───────────────────


@pytest.mark.asyncio
async def test_clone_populates_workspace(tmp_path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("seeded\n")
    project = _project(
        workspace_type=WorkspaceType.github_connected,
        repo_url="https://github.com/acme/widget.git",
    )

    async def _fake_clone(argv, *, cwd=None):
        # argv = ["git", "clone", ..., url, dest]; simulate populating dest.
        dest = Path(argv[-1])
        dest.mkdir(parents=True, exist_ok=True)
        (dest / ".git").mkdir()
        (dest / "README.md").write_text("real repo\n")
        (dest / "src").mkdir()
        (dest / "src" / "app.py").write_text("print('hi')\n")
        return 0, "", ""

    result = await ensure_repo_cloned(project=project, workspace_dir=workspace, clone_runner=_fake_clone)
    assert result.status == "cloned"
    assert (workspace / ".git").is_dir()
    assert (workspace / "README.md").read_text() == "real repo\n"
    assert (workspace / "src" / "app.py").read_text() == "print('hi')\n"
    # Seeded AGENTS.md is preserved when the repo does not carry its own.
    assert (workspace / "AGENTS.md").read_text() == "seeded\n"
    # No clone temp dir left behind.
    leftovers = [p.name for p in workspace.parent.iterdir() if p.name.startswith(".clone-")]
    assert leftovers == []


@pytest.mark.asyncio
async def test_repo_agents_md_wins_over_seeded(tmp_path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("seeded\n")
    project = _project(
        workspace_type=WorkspaceType.github_connected,
        repo_url="https://github.com/acme/widget.git",
    )

    async def _fake_clone(argv, *, cwd=None):
        dest = Path(argv[-1])
        dest.mkdir(parents=True, exist_ok=True)
        (dest / ".git").mkdir()
        (dest / "AGENTS.md").write_text("repo conventions\n")
        return 0, "", ""

    await ensure_repo_cloned(project=project, workspace_dir=workspace, clone_runner=_fake_clone)
    assert (workspace / "AGENTS.md").read_text() == "repo conventions\n"


@pytest.mark.asyncio
async def test_clone_failure_soft_fails_without_raising(tmp_path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    project = _project(
        workspace_type=WorkspaceType.github_connected,
        repo_url="https://github.com/acme/widget.git",
    )

    async def _failing_clone(argv, *, cwd=None):
        return 128, "", "fatal: repository not found"

    result = await ensure_repo_cloned(project=project, workspace_dir=workspace, clone_runner=_failing_clone)
    assert result.status == "failed"
    assert "repository not found" in (result.detail or "")
    assert not (workspace / ".git").exists()
    # Failed clone must not leave a temp dir behind.
    leftovers = [p.name for p in workspace.parent.iterdir() if p.name.startswith(".clone-")]
    assert leftovers == []


@pytest.mark.asyncio
async def test_clone_failure_redacts_token_from_detail(tmp_path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    project = _project(
        workspace_type=WorkspaceType.github_connected,
        repo_url="https://github.com/acme/widget.git",
        token_encrypted="enc",
    )

    async def _failing_clone(argv, *, cwd=None):
        return 128, "", "fatal: https://x-access-token:supersecret@github.com/x denied"

    result = await ensure_repo_cloned(
        project=project,
        workspace_dir=workspace,
        clone_runner=_failing_clone,
        decrypt=lambda _enc: "supersecret",
    )
    assert result.status == "failed"
    assert "supersecret" not in (result.detail or "")


# ───────────────────────────── real git integration ───────────────────


@pytest.mark.asyncio
async def test_real_git_clone_from_local_repo(tmp_path) -> None:
    """Exercises the real ``git`` subprocess against an offline
    ``file://`` repo — proves the default clone runner works."""
    # Build a tiny upstream repo.
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    subprocess.run(["git", "init", "-b", "main"], cwd=upstream, check=True, env=env)
    (upstream / "hello.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "."], cwd=upstream, check=True, env=env)
    subprocess.run(["git", "commit", "-m", "init"], cwd=upstream, check=True, env=env)

    workspace = tmp_path / "ws"
    workspace.mkdir()
    project = _project(
        workspace_type=WorkspaceType.github_connected,
        repo_url=f"file://{upstream}",
        branch="main",
    )
    result = await ensure_repo_cloned(project=project, workspace_dir=workspace)
    assert result.status == "cloned"
    assert (workspace / ".git").is_dir()
    assert (workspace / "hello.py").read_text() == "x = 1\n"


def test_clone_result_is_frozen() -> None:
    r = CloneResult(status="skipped", detail=None)
    with pytest.raises(Exception):
        r.status = "cloned"  # type: ignore[misc]
