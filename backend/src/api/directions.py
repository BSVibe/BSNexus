from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.auth import get_current_user
from backend.src.core.directions import create_direction
from backend.src.core.tenant_context import get_tenant_id
from backend.src.models import Direction, Project
from backend.src.schemas import DirectionCreate, DirectionResponse
from backend.src.storage.database import get_db

router = APIRouter(prefix="/api/v1/directions", tags=["directions"])


async def _assert_project_belongs(db: AsyncSession, project_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
    stmt = select(Project.id).where(Project.id == project_id, Project.tenant_id == tenant_id)
    if (await db.execute(stmt)).scalar_one_or_none() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")


@router.post("", response_model=DirectionResponse, status_code=status.HTTP_201_CREATED)
async def post_direction(
    payload: DirectionCreate,
    user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> Direction:
    if payload.project_id is not None:
        await _assert_project_belongs(db, payload.project_id, tenant_id)

    actor_id = str(getattr(user, "id", "unknown"))
    return await create_direction(
        payload=payload,
        tenant_id=tenant_id,
        actor_id=actor_id,
        session=db,
    )
