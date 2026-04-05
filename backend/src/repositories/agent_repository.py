"""Data access layer for Agent entities."""

from __future__ import annotations

import uuid

from sqlalchemy import select, update

from backend.src.models import Agent
from backend.src.repositories.base import BaseRepository


class AgentRepository(BaseRepository):
    """CRUD operations for Agent model."""

    async def get_by_id(self, agent_id: uuid.UUID) -> Agent | None:
        result = await self.db.execute(select(Agent).where(Agent.id == agent_id))
        return result.scalar_one_or_none()

    async def list_by_tenant(
        self,
        tenant_id: uuid.UUID,
        *,
        active_only: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Agent]:
        query = select(Agent).where(Agent.tenant_id == tenant_id)
        if active_only:
            query = query.where(Agent.is_active.is_(True))
        query = query.order_by(Agent.created_at).limit(limit).offset(offset)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def list_roots(self, tenant_id: uuid.UUID) -> list[Agent]:
        """Get top-level agents (no parent) for org chart root."""
        query = (
            select(Agent)
            .where(Agent.tenant_id == tenant_id, Agent.parent_agent_id.is_(None), Agent.is_active.is_(True))
            .order_by(Agent.created_at)
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def list_children(self, parent_id: uuid.UUID) -> list[Agent]:
        """Get direct children of an agent."""
        query = (
            select(Agent)
            .where(Agent.parent_agent_id == parent_id, Agent.is_active.is_(True))
            .order_by(Agent.created_at)
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def update_fields(self, agent_id: uuid.UUID, **fields: object) -> Agent | None:
        """Update specific fields on an agent."""
        stmt = update(Agent).where(Agent.id == agent_id).values(**fields)
        await self.db.execute(stmt)
        await self.db.flush()
        return await self.get_by_id(agent_id)

    async def count_by_tenant(self, tenant_id: uuid.UUID) -> int:
        from sqlalchemy import func

        result = await self.db.execute(
            select(func.count()).select_from(Agent).where(Agent.tenant_id == tenant_id, Agent.is_active.is_(True))
        )
        return result.scalar_one()
