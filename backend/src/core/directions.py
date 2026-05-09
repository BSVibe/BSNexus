from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.models import Direction
from backend.src.schemas import DirectionCreate


async def create_direction(
    *,
    payload: DirectionCreate,
    tenant_id: uuid.UUID,
    actor_id: str,
    session: AsyncSession,
) -> Direction:
    direction = Direction(
        tenant_id=tenant_id,
        project_id=payload.project_id,
        source=payload.source,
        actor_id=actor_id,
        body=payload.body,
        target_hint=payload.target_hint,
    )
    session.add(direction)
    await session.commit()
    await session.refresh(direction)
    return direction
