"""Agent long-term memory providers.

The default ``LocalMemoryProvider`` reads and writes the agent_memories
table in the local database. Projects that opt into BSage swap in
``BSageMemoryProvider`` (a thin REST client) via project settings —
either provider satisfies the same ``MemoryProvider`` protocol so the
rest of the codebase doesn't care which backend is configured.

The current implementation ships only the local provider. The BSage
provider will land alongside the BSage credential UI in a follow-up.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.models import AgentMemory


@dataclass
class MemoryRecord:
    id: uuid.UUID
    project_id: uuid.UUID
    agent_id: uuid.UUID | None
    category: str
    title: str
    content: str
    metadata: dict | None = None


class MemoryProvider(Protocol):
    """Async interface every memory backend must satisfy."""

    async def remember(
        self,
        project_id: uuid.UUID,
        agent_id: uuid.UUID | None,
        *,
        category: str,
        title: str,
        content: str,
        metadata: dict | None = None,
    ) -> MemoryRecord: ...

    async def recall(
        self,
        project_id: uuid.UUID,
        agent_id: uuid.UUID | None = None,
        *,
        category: str | None = None,
        limit: int = 20,
    ) -> list[MemoryRecord]: ...

    async def forget(self, memory_id: uuid.UUID) -> bool: ...


class LocalMemoryProvider:
    """Default provider — persists memories in the project DB."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def remember(
        self,
        project_id: uuid.UUID,
        agent_id: uuid.UUID | None,
        *,
        category: str,
        title: str,
        content: str,
        metadata: dict | None = None,
    ) -> MemoryRecord:
        row = AgentMemory(
            project_id=project_id,
            agent_id=agent_id,
            category=category,
            title=title,
            content=content,
            extra_metadata=metadata,
        )
        self._db.add(row)
        await self._db.flush()
        return _to_record(row)

    async def recall(
        self,
        project_id: uuid.UUID,
        agent_id: uuid.UUID | None = None,
        *,
        category: str | None = None,
        limit: int = 20,
    ) -> list[MemoryRecord]:
        query = select(AgentMemory).where(AgentMemory.project_id == project_id)
        if agent_id is not None:
            query = query.where(AgentMemory.agent_id == agent_id)
        if category is not None:
            query = query.where(AgentMemory.category == category)
        query = query.order_by(AgentMemory.created_at.desc()).limit(limit)
        result = await self._db.execute(query)
        return [_to_record(row) for row in result.scalars().all()]

    async def forget(self, memory_id: uuid.UUID) -> bool:
        row = await self._db.get(AgentMemory, memory_id)
        if row is None:
            return False
        await self._db.delete(row)
        return True


def _to_record(row: AgentMemory) -> MemoryRecord:
    return MemoryRecord(
        id=row.id,
        project_id=row.project_id,
        agent_id=row.agent_id,
        category=row.category,
        title=row.title,
        content=row.content,
        metadata=row.extra_metadata,
    )
