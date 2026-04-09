"""Workspace service — coordinates storage backend + git for project workspaces."""

from __future__ import annotations

import asyncio
import uuid

import structlog

from backend.src.core.git_ops import GitOps
from backend.src.core.workspace.storage_backend import FileInfo, StorageBackend

logger = structlog.get_logger(__name__)

# Per-project lock for serializing git push/pull operations
_project_locks: dict[str, asyncio.Lock] = {}


class WorkspaceService:
    """High-level workspace operations for projects.

    Delegates file I/O to a StorageBackend and uses GitOps for version control.
    """

    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage

    # ── lifecycle ─────────────────────────────────────────────────

    async def create_workspace(self, project_id: uuid.UUID) -> str:
        """Create a new workspace directory. Returns the absolute path."""
        workspace_id = str(project_id)
        root = await self._storage.init_workspace(workspace_id)
        logger.info("workspace_created", project_id=workspace_id, path=root)
        return root

    async def cleanup_workspace(self, project_id: uuid.UUID) -> None:
        """Delete workspace and all contents (project deletion)."""
        workspace_id = str(project_id)
        await self._storage.delete_workspace(workspace_id)
        logger.info("workspace_deleted", project_id=workspace_id)

    # ── file operations ──────────────────────────────────────────

    async def list_files(
        self, project_id: uuid.UUID, path: str = "", *, recursive: bool = False
    ) -> list[FileInfo]:
        return await self._storage.list_files(str(project_id), path, recursive=recursive)

    async def read_file(self, project_id: uuid.UUID, path: str) -> bytes:
        return await self._storage.read_file(str(project_id), path)

    async def write_file(self, project_id: uuid.UUID, path: str, content: bytes) -> None:
        await self._storage.write_file(str(project_id), path, content)

    async def file_exists(self, project_id: uuid.UUID, path: str) -> bool:
        return await self._storage.exists(str(project_id), path)

    def get_workspace_path(self, project_id: uuid.UUID) -> str:
        """Get the absolute filesystem path for a workspace."""
        return self._storage.get_workspace_path(str(project_id))

    # ── GitHub integration ───────────────────────────────────────

    def _lock(self, project_id: uuid.UUID) -> asyncio.Lock:
        key = str(project_id)
        if key not in _project_locks:
            _project_locks[key] = asyncio.Lock()
        return _project_locks[key]

    async def connect_github(
        self,
        project_id: uuid.UUID,
        repo_url: str,
        token: str,
        branch: str = "main",
    ) -> str:
        """Clone a GitHub repo into the workspace. Returns workspace path."""
        workspace_id = str(project_id)
        workspace_path = self._storage.get_workspace_path(workspace_id)

        # Inject token into HTTPS URL for auth: https://TOKEN@github.com/...
        auth_url = repo_url.replace("https://", f"https://{token}@")

        async with self._lock(project_id):
            # Clean existing workspace and clone
            await self._storage.delete_workspace(workspace_id)
            git = GitOps(workspace_path)
            await git.clone(auth_url, branch)
            # Reset remote to tokenless URL (token stored in DB, injected at push/pull time)
            await git.add_remote("origin", repo_url)

        logger.info("github_connected", project_id=workspace_id, repo=repo_url, branch=branch)
        return workspace_path

    async def sync_from_github(
        self,
        project_id: uuid.UUID,
        token: str,
        repo_url: str,
        branch: str = "main",
    ) -> None:
        """Pull latest changes from GitHub before task execution."""
        workspace_path = self._storage.get_workspace_path(str(project_id))
        git = GitOps(workspace_path)

        auth_url = repo_url.replace("https://", f"https://{token}@")
        async with self._lock(project_id):
            await git.add_remote("origin", auth_url)
            try:
                await git.pull("origin", branch)
            finally:
                # Reset to tokenless URL
                await git.add_remote("origin", repo_url)

        logger.info("github_synced", project_id=str(project_id))

    async def push_to_github(
        self,
        project_id: uuid.UUID,
        token: str,
        repo_url: str,
        branch: str = "main",
        message: str = "chore: agent task results",
    ) -> str:
        """Commit all changes and push to GitHub. Returns commit hash."""
        workspace_path = self._storage.get_workspace_path(str(project_id))
        git = GitOps(workspace_path)

        auth_url = repo_url.replace("https://", f"https://{token}@")
        async with self._lock(project_id):
            # Stage and commit
            commit_hash = await git.commit_task("auto", message, branch)
            if not commit_hash:
                logger.info("github_push_skipped", project_id=str(project_id), reason="no changes")
                return ""
            # Push with auth
            await git.add_remote("origin", auth_url)
            try:
                await git.push("origin", branch)
            finally:
                await git.add_remote("origin", repo_url)

        logger.info("github_pushed", project_id=str(project_id), commit=commit_hash)
        return commit_hash
