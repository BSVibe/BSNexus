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
        task_id: uuid.UUID | None = None,
    ) -> list[ConversationMessage]:
        """Return the most recent N messages for a project, oldest first.

        If ``task_id`` is provided, only return messages linked to that task.
        """
        query = (
            select(ConversationMessage)
            .where(ConversationMessage.project_id == project_id)
        )
        if task_id is not None:
            query = query.where(ConversationMessage.task_id == task_id)
        result = await self.db.execute(
            query.order_by(ConversationMessage.created_at.desc()).limit(limit)
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
        task_id: uuid.UUID | None = None,
        message_id: uuid.UUID | None = None,
    ) -> ConversationMessage:
        kwargs: dict[str, Any] = {
            "project_id": project_id,
            "role": role,
            "content": content,
            "agent_id": agent_id,
            "agent_name": agent_name,
            "actions": actions or [],
            "source": source,
            "external_id": external_id,
            "thread_ref": thread_ref,
            "task_id": task_id,
        }
        if message_id is not None:
            kwargs["id"] = message_id
        msg = ConversationMessage(**kwargs)
        await self.add(msg)
        await self.refresh(msg)
        return msg

    async def clear(self, project_id: uuid.UUID) -> None:
        await self.db.execute(
            delete(ConversationMessage).where(ConversationMessage.project_id == project_id)
        )
