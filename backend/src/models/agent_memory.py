"""Agent memory model — cross-conversation knowledge persistence.

Each memory entry belongs to a project + agent pair and captures a
decision, lesson, or fact the agent learned during a work session.
Entries are loaded into the agent's context at the start of each
conversation so prior experience informs new work.

Storage backend is the local DB by default; an optional BSage provider
can sync entries to an external service when the user configures it.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from backend.src.storage.database import Base


class AgentMemory(Base):
    __tablename__ = "agent_memories"
    __table_args__ = (
        Index("ix_agent_memories_project_agent", "project_id", "agent_id"),
        Index("ix_agent_memories_project_created", "project_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )

    # Categorize the memory for retrieval relevance scoring.
    category: Mapped[str] = mapped_column(
        String(64), nullable=False, default="decision"
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # ``metadata`` is reserved on Declarative bases — store the user-facing
    # field under the column name "metadata" but expose it as extra_metadata.
    extra_metadata: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
