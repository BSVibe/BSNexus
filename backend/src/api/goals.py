"""Goal CRUD API — manage hierarchical goal alignment cascade."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.goal_alignment import GoalAlignmentService
from backend.src.models import Goal
from backend.src.schemas.goal import GoalAncestryResponse, GoalCreate, GoalResponse, GoalUpdate
from backend.src.storage.database import get_db

router = APIRouter(prefix="/api/v1/goals", tags=["goals"])

_DEFAULT_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


@router.post("", response_model=GoalResponse, status_code=201)
async def create_goal(body: GoalCreate, db: AsyncSession = Depends(get_db)) -> GoalResponse:
    goal = Goal(
        tenant_id=_DEFAULT_TENANT_ID,
        title=body.title,
        description=body.description,
        level=body.level,
        parent_goal_id=body.parent_goal_id,
        project_id=body.project_id,
        agent_id=body.agent_id,
    )
    db.add(goal)
    await db.flush()
    await db.commit()
    await db.refresh(goal)
    return GoalResponse.model_validate(goal)


@router.get("", response_model=list[GoalResponse])
async def list_goals(
    level: str | None = None,
    project_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[GoalResponse]:
    query = select(Goal).where(Goal.tenant_id == _DEFAULT_TENANT_ID)
    if level:
        query = query.where(Goal.level == level)
    if project_id:
        query = query.where(Goal.project_id == project_id)
    query = query.order_by(Goal.created_at)
    result = await db.execute(query)
    return [GoalResponse.model_validate(g) for g in result.scalars().all()]


@router.get("/{goal_id}", response_model=GoalResponse)
async def get_goal(goal_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> GoalResponse:
    result = await db.execute(select(Goal).where(Goal.id == goal_id))
    goal = result.scalar_one_or_none()
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    return GoalResponse.model_validate(goal)


@router.get("/{goal_id}/ancestry", response_model=GoalAncestryResponse)
async def get_goal_ancestry(goal_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> GoalAncestryResponse:
    service = GoalAlignmentService(db)
    chain = await service.get_goal_ancestry(goal_id)
    return GoalAncestryResponse(chain=[GoalResponse.model_validate(g) for g in chain])


@router.patch("/{goal_id}", response_model=GoalResponse)
async def update_goal(
    goal_id: uuid.UUID,
    body: GoalUpdate,
    db: AsyncSession = Depends(get_db),
) -> GoalResponse:
    result = await db.execute(select(Goal).where(Goal.id == goal_id))
    goal = result.scalar_one_or_none()
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(goal, key, value)
    await db.flush()
    await db.commit()
    await db.refresh(goal)
    return GoalResponse.model_validate(goal)


@router.delete("/{goal_id}", status_code=204)
async def delete_goal(goal_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> None:
    result = await db.execute(select(Goal).where(Goal.id == goal_id))
    goal = result.scalar_one_or_none()
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    await db.delete(goal)
    await db.commit()
