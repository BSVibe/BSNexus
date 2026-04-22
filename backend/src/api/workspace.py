"""Workspace API — file browsing and GitHub integration for project workspaces."""

from __future__ import annotations

import base64
import mimetypes
import os
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.encryption import EncryptionManager
from backend.src.core.workspace import LocalStorageBackend, WorkspaceService
from backend.src.models import Project, WorkspaceType
from backend.src.repositories.project_repository import ProjectRepository
from backend.src.storage.database import get_db

_encryption = EncryptionManager(os.environ.get("ENCRYPTION_KEY", "dev-encryption-key-not-for-production-00"))

router = APIRouter(prefix="/api/v1/projects", tags=["workspace"])

_workspace_service = WorkspaceService(
    LocalStorageBackend(os.environ.get("WORKSPACE_BASE_DIR", "data/workspaces"))
)


# ── Schemas ──────────────────────────────────────────────────────

class FileInfoResponse(BaseModel):
    path: str
    name: str
    is_dir: bool
    size: int = 0
    modified_at: Optional[datetime] = None


class FileListResponse(BaseModel):
    files: list[FileInfoResponse]
    current_path: str


class FileContentResponse(BaseModel):
    path: str
    content: str
    is_binary: bool
    size: int


# ── Helpers ──────────────────────────────────────────────────────

_TEXT_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml",
    ".toml", ".md", ".txt", ".html", ".css", ".scss", ".sh", ".bash",
    ".sql", ".xml", ".csv", ".env", ".gitignore", ".dockerignore",
    ".rs", ".go", ".java", ".kt", ".swift", ".rb", ".php", ".c",
    ".cpp", ".h", ".hpp", ".lock", ".cfg", ".ini", ".conf",
    "Makefile", "Dockerfile", "Procfile",
}


def _is_text_file(path: str) -> bool:
    _, ext = os.path.splitext(path)
    if ext.lower() in _TEXT_EXTENSIONS:
        return True
    mime, _ = mimetypes.guess_type(path)
    return mime is not None and mime.startswith("text/")


async def _get_project(project_id: uuid.UUID, db: AsyncSession) -> Project:
    repo = ProjectRepository(db)
    project = await repo.get_by_id(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.workspace_dir:
        raise HTTPException(status_code=400, detail="Project has no workspace")
    return project


# ── Endpoints ────────────────────────────────────────────────────

@router.get("/{project_id}/files", response_model=FileListResponse)
async def list_files(
    project_id: uuid.UUID,
    path: str = Query("", description="Subdirectory to list"),
    recursive: bool = Query(False),
    db: AsyncSession = Depends(get_db),
) -> FileListResponse:
    """List files in a project workspace."""
    project = await _get_project(project_id, db)

    try:
        files = await _workspace_service.list_files(project.id, path, recursive=recursive)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Path traversal not allowed")

    return FileListResponse(
        files=[FileInfoResponse(**f.__dict__) for f in files],
        current_path=path,
    )


@router.get("/{project_id}/files/content", response_model=FileContentResponse)
async def read_file(
    project_id: uuid.UUID,
    path: str = Query(..., description="File path relative to workspace root"),
    db: AsyncSession = Depends(get_db),
) -> FileContentResponse:
    """Read a file from the project workspace."""
    project = await _get_project(project_id, db)

    try:
        data = await _workspace_service.read_file(project.id, path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    except PermissionError:
        raise HTTPException(status_code=403, detail="Path traversal not allowed")

    is_binary = not _is_text_file(path)
    if is_binary:
        content = base64.b64encode(data).decode("ascii")
    else:
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            content = base64.b64encode(data).decode("ascii")
            is_binary = True

    return FileContentResponse(
        path=path,
        content=content,
        is_binary=is_binary,
        size=len(data),
    )


# ── GitHub Integration ───────────────────────────────────────────

class GitHubConnectRequest(BaseModel):
    repo_url: str  # HTTPS URL: https://github.com/owner/repo
    token: str     # GitHub PAT
    branch: str = "main"


class GitHubStatusResponse(BaseModel):
    connected: bool
    repo_url: Optional[str] = None
    branch: Optional[str] = None


@router.post("/{project_id}/github/connect", response_model=GitHubStatusResponse)
async def connect_github(
    project_id: uuid.UUID,
    body: GitHubConnectRequest,
    db: AsyncSession = Depends(get_db),
) -> GitHubStatusResponse:
    """Connect a GitHub repo — clones into the project workspace."""
    repo = ProjectRepository(db)
    project = await repo.get_by_id(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Clone into workspace
    try:
        workspace_dir = await _workspace_service.connect_github(
            project.id, body.repo_url, body.token, body.branch,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to clone: {e}")

    # Update project
    project.workspace_type = WorkspaceType.github_connected
    project.workspace_dir = workspace_dir
    project.github_repo_url = body.repo_url
    project.github_branch = body.branch
    project.github_token_encrypted = _encryption.encrypt_value(body.token)
    await db.commit()

    return GitHubStatusResponse(connected=True, repo_url=body.repo_url, branch=body.branch)


@router.post("/{project_id}/github/sync")
async def sync_github(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Pull latest changes from GitHub."""
    project = await _get_github_project(project_id, db)
    token = _encryption.decrypt_value(project.github_token_encrypted)

    try:
        await _workspace_service.sync_from_github(
            project.id, token, project.github_repo_url, project.github_branch or "main",
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Sync failed: {e}")

    return {"status": "synced"}


@router.post("/{project_id}/github/push")
async def push_github(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Commit and push changes to GitHub."""
    project = await _get_github_project(project_id, db)
    token = _encryption.decrypt_value(project.github_token_encrypted)

    try:
        commit_hash = await _workspace_service.push_to_github(
            project.id, token, project.github_repo_url, project.github_branch or "main",
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Push failed: {e}")

    return {"status": "pushed", "commit": commit_hash or "no changes"}


@router.delete("/{project_id}/github")
async def disconnect_github(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Disconnect GitHub — keeps workspace files but removes remote config."""
    repo = ProjectRepository(db)
    project = await repo.get_by_id(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    project.workspace_type = WorkspaceType.server_managed
    project.github_repo_url = None
    project.github_branch = None
    project.github_token_encrypted = None
    await db.commit()

    return {"status": "disconnected"}


@router.get("/{project_id}/github/status", response_model=GitHubStatusResponse)
async def github_status(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> GitHubStatusResponse:
    """Get GitHub connection status."""
    repo = ProjectRepository(db)
    project = await repo.get_by_id(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    connected = project.workspace_type == WorkspaceType.github_connected and project.github_repo_url is not None
    return GitHubStatusResponse(
        connected=connected,
        repo_url=project.github_repo_url,
        branch=project.github_branch,
    )


async def _get_github_project(project_id: uuid.UUID, db: AsyncSession) -> Project:
    repo = ProjectRepository(db)
    project = await repo.get_by_id(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.workspace_type != WorkspaceType.github_connected:
        raise HTTPException(status_code=400, detail="Project is not connected to GitHub")
    if not project.github_token_encrypted:
        raise HTTPException(status_code=400, detail="GitHub token not configured")
    return project
