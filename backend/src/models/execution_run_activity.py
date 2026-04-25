"""ExecutionRunActivity — structured event log for the Inside panel.

Two granularities share the table:

- ``milestone``: user-facing run-level events (started, completed, failed,
  progress note, artifact attached). The Inside timeline shows these.
- ``tool``: raw tool calls a worker made (read file, exec command, write
  file, llm call). The Inside timeline hides them behind a "Show tool
  log" toggle.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.src.storage.database import Base


class ActivityLevel(str, enum.Enum):
    milestone = "milestone"
    tool = "tool"


class ExecutionRunActivity(Base):
    __tablename__ = "execution_run_activities"
    __table_args__ = (
        Index("ix_execution_run_activities_run_created", "run_id", "created_at"),
        Index("ix_execution_run_activities_project_created", "project_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("execution_runs.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)

    level: Mapped[ActivityLevel] = mapped_column(Enum(ActivityLevel), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    run: Mapped["ExecutionRun"] = relationship("ExecutionRun")  # noqa: F821
