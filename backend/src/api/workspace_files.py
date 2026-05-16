"""Workspace files admin (G7.5c).

Read-only view of a project's workspace directory. The founder needs
this for design + code reviews — without it, deliverables that
reference paths inside the workspace (``Deliverable.artifact_refs``)
have nowhere to land.

Endpoints:
  GET /api/v1/workspace-files?project_id=X&path=Y      — directory listing
  GET /api/v1/workspace-files/content?project_id=X&path=Y — file content

Both are tenant-scoped via the project lookup. Path traversal is
explicitly blocked: any path that resolves outside the workspace
root, contains absolute prefixes, or includes ``..`` segments → 422.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.auth import get_current_user, require_permission
from backend.src.core.tenant_context import get_tenant_id
from backend.src.models.project import Project
from backend.src.schemas.workspace import (
    WorkspaceContentResponse,
    WorkspaceEntry,
    WorkspaceTreeResponse,
)
from backend.src.storage.database import get_db

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/workspace-files", tags=["workspace-files"])

MAX_FILE_BYTES = 256 * 1024  # 256 KiB hard cap; anything larger is "view in repo".


async def _load_project(db: AsyncSession, project_id: uuid.UUID, tenant_id: uuid.UUID) -> Project:
    stmt = select(Project).where(Project.id == project_id, Project.tenant_id == tenant_id)
    project = (await db.execute(stmt)).scalar_one_or_none()
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


def _resolve_safe_path(workspace_root: Path, sub: str) -> Path:
    """Resolve ``sub`` against ``workspace_root`` and confirm the result
    stays inside the root. Anything else 422s."""
    if sub.startswith("/") or sub.startswith("\\"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Absolute paths are not allowed")
    if ".." in Path(sub).parts:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Parent traversal is not allowed")
    target = (workspace_root / sub).resolve()
    root_resolved = workspace_root.resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Path escapes workspace root")
    return target


@router.get(
    "",
    response_model=WorkspaceTreeResponse,
    dependencies=[Depends(require_permission("bsnexus.workspace_files.read"))],
)
async def list_tree(
    project_id: uuid.UUID,
    path: str = Query("", max_length=1000),
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceTreeResponse:
    project = await _load_project(db, project_id, tenant_id)
    if not project.workspace_dir:
        return WorkspaceTreeResponse(path=path, entries=[])

    workspace_root = Path(project.workspace_dir)
    if not workspace_root.exists():
        return WorkspaceTreeResponse(path=path, entries=[])

    target = _resolve_safe_path(workspace_root, path)
    if not target.exists() or not target.is_dir():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Directory not found")

    entries: list[WorkspaceEntry] = []
    for child in sorted(target.iterdir(), key=lambda p: p.name):
        if child.name.startswith("."):
            continue
        is_dir = child.is_dir()
        entries.append(
            WorkspaceEntry(
                name=child.name,
                kind="dir" if is_dir else "file",
                size=None if is_dir else child.stat().st_size,
            )
        )
    return WorkspaceTreeResponse(path=path, entries=entries)


@router.get(
    "/content",
    response_model=WorkspaceContentResponse,
    dependencies=[Depends(require_permission("bsnexus.workspace_files.read"))],
)
async def read_content(
    project_id: uuid.UUID,
    path: str = Query(..., min_length=1, max_length=1000),
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceContentResponse:
    project = await _load_project(db, project_id, tenant_id)
    if not project.workspace_dir:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workspace not configured")

    workspace_root = Path(project.workspace_dir)
    target = _resolve_safe_path(workspace_root, path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")

    size = target.stat().st_size
    if size > MAX_FILE_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File exceeds {MAX_FILE_BYTES} byte cap",
        )

    raw = target.read_bytes()
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "Binary file — no inline view",
        )

    return WorkspaceContentResponse(path=path, content=content, size=size)
