"""Workspace files API — flat resource shape (decision-locks A3, 2026-05-08).

The orchestrator's post-completion hook (``core.run_artifacts``) writes
extracted fenced code blocks into a per-project workspace directory.
This router surfaces them so the frontend's Files tab can show a real
tree + file contents.

- ``GET /api/v1/workspace-files?project_id={id}``                  → tree listing
- ``GET /api/v1/workspace-files/content?project_id={id}&path={p}`` → file content
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core import project_workspace as workspace_store
from backend.src.core.auth import get_current_user
from backend.src.core.tenant_context import get_tenant_id
from backend.src.models import Project
from backend.src.storage.database import get_db

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/workspace-files", tags=["workspace-files"])


async def _require_project(db: AsyncSession, project_id: uuid.UUID, tenant_id: uuid.UUID) -> Project:
    stmt = select(Project).where(Project.id == project_id, Project.tenant_id == tenant_id)
    project = (await db.execute(stmt)).scalar_one_or_none()
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


@router.get("")
async def list_workspace_files(
    project_id: uuid.UUID = Query(..., description="Project to list workspace files for."),
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    await _require_project(db, project_id, tenant_id)
    return workspace_store.list_files(project_id)


@router.get("/content")
async def read_workspace_file(
    project_id: uuid.UUID = Query(..., description="Project the file lives in."),
    path: str = Query(..., description="Relative path within the project workspace."),
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await _require_project(db, project_id, tenant_id)
    content = workspace_store.read_file(project_id, path)
    if content is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    return {"path": path, "content": content}
