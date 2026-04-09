"""Data access for ConversationMessage — project chat history."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, select

from backend.src.models import ConversationMessage
from backend.src.repositories.base import BaseRepository


class ConversationRepository(BaseRepository):
    """CRUD for project chat messages."""

    async def list_by_project(
        self,
        project_id: uuid.UUID,
        *,
        limit: int = 100,
    ) -> list[ConversationMessage]:
        """Return the most recent N messages for a project, oldest first."""
        result = await self.db.execute(
            select(ConversationMessage)
            .where(ConversationMessage.project_id == project_id)
            .order_by(ConversationMessage.created_at.desc())
            .limit(limit)
        )
        rows = list(result.scalars().all())
        rows.reverse()
        return rows

    async def append(
        self,
        project_id: uuid.UUID,
        *,
        role: str,
        content: str,
        agent_id: uuid.UUID | None = None,
        agent_name: str | None = None,
        actions: list[dict[str, Any]] | None = None,
        source: str = "web",
        external_id: str | None = None,
        thread_ref: str | None = None,
    ) -> ConversationMessage:
        msg = ConversationMessage(
            project_id=project_id,
            role=role,
            content=content,
            agent_id=agent_id,
            agent_name=agent_name,
            actions=actions or [],
            source=source,
            external_id=external_id,
            thread_ref=thread_ref,
        )
        await self.add(msg)
        await self.refresh(msg)
        return msg

    async def clear(self, project_id: uuid.UUID) -> None:
        await self.db.execute(
            delete(ConversationMessage).where(ConversationMessage.project_id == project_id)
        )
