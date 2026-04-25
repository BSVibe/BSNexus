"""Workspace files API — list + read the real files produced by runs.

The orchestrator's post-completion hook (``core.run_artifacts``) writes
extracted fenced code blocks into a per-project workspace directory.
This router surfaces them so the frontend's Files tab can show a real
tree + file contents, not a hardcoded sample.

Endpoints:
- ``GET /api/v1/projects/{project_id}/files``     → tree listing
- ``GET /api/v1/projects/{project_id}/files/{p}`` → single file content
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core import project_workspace as workspace_store
from backend.src.core.auth import get_current_user
from backend.src.core.tenant_context import get_tenant_id
from backend.src.models import Project
from backend.src.storage.database import get_db

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/projects", tags=["workspace-files"])


async def _require_project(db: AsyncSession, project_id: uuid.UUID, tenant_id: uuid.UUID) -> Project:
    stmt = select(Project).where(Project.id == project_id, Project.tenant_id == tenant_id)
    project = (await db.execute(stmt)).scalar_one_or_none()
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


@router.get("/{project_id}/files")
async def list_workspace_files(
    project_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
) -> list[dict]:
    await _require_project(db, project_id, tenant_id)
    return workspace_store.list_files(project_id)


@router.get("/{project_id}/files/{path:path}")
async def read_workspace_file(
    project_id: uuid.UUID,
    path: str,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
) -> dict:
    await _require_project(db, project_id, tenant_id)
    content = workspace_store.read_file(project_id, path)
    if content is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    return {"path": path, "content": content}
