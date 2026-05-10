from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.auth import get_current_user
from backend.src.core.brief import build_brief_snapshot
from backend.src.core.tenant_context import get_tenant_id
from backend.src.models import Project
from backend.src.schemas import BriefSnapshotResponse
from backend.src.storage.database import get_db

router = APIRouter(prefix="/api/v1/brief", tags=["brief"])


async def _assert_project_belongs(db: AsyncSession, project_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
    stmt = select(Project.id).where(Project.id == project_id, Project.tenant_id == tenant_id)
    if (await db.execute(stmt)).scalar_one_or_none() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")


@router.get("", response_model=BriefSnapshotResponse)
async def get_brief(
    project_id: uuid.UUID | None = Query(None),
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if project_id is not None:
        await _assert_project_belongs(db, project_id, tenant_id)
    return await build_brief_snapshot(session=db, tenant_id=tenant_id, project_id=project_id)
