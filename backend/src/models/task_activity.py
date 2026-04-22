"""TaskActivity model — structured event log for the Plan view detail panel.

Two levels of granularity live in the same table:

- ``milestone`` events are user-facing: task started, completed, failed,
  progress note from the agent, artifact attached. The Plan detail panel
  shows these by default.
- ``tool`` events are the raw tool calls a worker made (read file, exec
  command, write file, llm call). The detail panel hides them behind a
  "Show tool log" toggle.

Both kinds carry an event_type, a human-readable summary, and an
optional structured ``detail`` payload (file paths, exit codes, diffs).
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


class TaskActivity(Base):
    __tablename__ = "task_activities"
    __table_args__ = (
        Index("ix_task_activities_task_created", "task_id", "created_at"),
        Index("ix_task_activities_project_created", "project_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )

    level: Mapped[ActivityLevel] = mapped_column(Enum(ActivityLevel), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    task: Mapped["Task"] = relationship("Task")  # noqa: F821
