"""ProjectDecision — confirmed project directions that all agents must respect.

A decision is created when a decision-making agent (typically C-level)
wraps text in ``[DECISION] ... [/DECISION]`` markers. Active decisions
are injected into every agent's system prompt so no one works against
a confirmed direction.

Decisions can be superseded: when a newer decision on the same topic is
created, the old one's ``is_active`` flag is set to False. The topic
matching is left to the agent — humans can also deactivate decisions
via the API.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.src.storage.database import Base


class ProjectDecision(Base):
    __tablename__ = "project_decisions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    # The agent that made the decision (nullable for user-created decisions)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    agent_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    title: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
