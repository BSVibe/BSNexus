from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from backend.src.models import DesignMessage, DesignSession, DesignSessionStatus, MessageRole, MessageType
from backend.src.repositories.base import BaseRepository


class DesignSessionRepository(BaseRepository):
    """Data access layer for DesignSession and DesignMessage entities."""

    async def get_by_id(self, session_id: uuid.UUID, *, load_messages: bool = True) -> DesignSession | None:
        """Get a session by ID with optional message loading."""
        query = select(DesignSession).where(DesignSession.id == session_id)
        if load_messages:
            query = query.options(selectinload(DesignSession.messages))
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def list_sessions(
        self,
        *,
        status: DesignSessionStatus | None = None,
        load_messages: bool = True,
    ) -> list[DesignSession]:
        """List all design sessions, optionally filtered by status."""
        query = select(DesignSession).order_by(DesignSession.created_at.desc())
        if status is not None:
            query = query.where(DesignSession.status == status)
        if load_messages:
            query = query.options(selectinload(DesignSession.messages))
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_by_project_id(self, project_id: uuid.UUID, *, load_messages: bool = True) -> DesignSession | None:
        """Get the project-bound session for a project."""
        query = (
            select(DesignSession)
            .where(
                DesignSession.project_id == project_id,
                DesignSession.status == DesignSessionStatus.project_bound,
            )
            .order_by(DesignSession.updated_at.desc())
        )
        if load_messages:
            query = query.options(selectinload(DesignSession.messages))
        result = await self.db.execute(query)
        return result.scalars().first()

    async def add_message(
        self,
        session_id: uuid.UUID,
        role: MessageRole,
        content: str,
        message_type: MessageType = MessageType.chat,
    ) -> DesignMessage:
        """Add a message to a session."""
        msg = DesignMessage(session_id=session_id, role=role, content=content, message_type=message_type)
        self.db.add(msg)
        await self.db.flush()
        return msg
