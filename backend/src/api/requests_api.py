from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.auth import get_current_user
from backend.src.core.tenant_context import get_tenant_id
from backend.src.models import Project, Request
from backend.src.schemas import RequestResponse
from backend.src.storage.database import get_db

router = APIRouter(prefix="/api/v1/requests", tags=["requests"])

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


async def _assert_project_belongs(db: AsyncSession, project_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
    stmt = select(Project.id).where(Project.id == project_id, Project.tenant_id == tenant_id)
    if (await db.execute(stmt)).scalar_one_or_none() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")


@router.get("", response_model=list[RequestResponse])
async def list_requests(
    project_id: uuid.UUID | None = Query(None),
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> list[Request]:
    if project_id is not None:
        await _assert_project_belongs(db, project_id, tenant_id)

    stmt = select(Request).where(Request.tenant_id == tenant_id)
    if project_id is not None:
        stmt = stmt.where(Request.project_id == project_id)
    stmt = stmt.order_by(Request.created_at.desc()).limit(limit)
    return list((await db.execute(stmt)).scalars())
