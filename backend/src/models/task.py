"""Task model with simplified 4-state status enum."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.src.storage.database import Base


class TaskStatus(str, enum.Enum):
    """Simplified 4-state lifecycle.

    - pending: not yet started (was: waiting, ready)
    - running: actively being worked on (was: in_progress, review)
    - blocked: stuck and needs intervention (was: redesign, plus failure cases)
    - done: completed
    """

    pending = "pending"
    running = "running"
    blocked = "blocked"
    done = "done"


class TaskPriority(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class TaskType(str, enum.Enum):
    feature = "feature"
    bug = "bug"
    improvement = "improvement"
    test = "test"
    chore = "chore"
    refactor = "refactor"


class TaskSource(str, enum.Enum):
    llm = "llm"
    auto_bug = "auto_bug"
    manual = "manual"


# Association table: task → tasks it depends on
task_dependencies = Table(
    "task_dependencies",
    Base.metadata,
    Column("task_id", Uuid, ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True),
    Column("dependency_id", Uuid, ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True),
)


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_project_status", "project_id", "status"),
        Index("ix_tasks_phase_status", "phase_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    phase_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("phases.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[TaskStatus] = mapped_column(Enum(TaskStatus), nullable=False, default=TaskStatus.pending)
    priority: Mapped[TaskPriority] = mapped_column(Enum(TaskPriority), nullable=False, default=TaskPriority.medium)
    task_type: Mapped[TaskType] = mapped_column(Enum(TaskType), nullable=False, default=TaskType.feature)
    source: Mapped[TaskSource] = mapped_column(Enum(TaskSource), nullable=False, default=TaskSource.llm)
    parent_task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    worker_prompt: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    qa_prompt: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    branch_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    commit_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    qa_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    goal_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("goals.id", ondelete="SET NULL"), nullable=True
    )
    executor_type: Mapped[str] = mapped_column(String(50), nullable=False, default="coding", server_default="coding")
    executor_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")
    output_type: Mapped[str | None] = mapped_column(String(50), nullable=True)  # "code_diff", "document", "report"
    output_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    qa_feedback_history: Mapped[list | None] = mapped_column(JSON, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    project: Mapped["Project"] = relationship("Project")  # noqa: F821
    phase: Mapped["Phase"] = relationship("Phase", back_populates="tasks")  # noqa: F821
    history: Mapped[list["TaskHistory"]] = relationship(
        "TaskHistory", back_populates="task", cascade="all, delete-orphan"
    )

    # Self-referential: parent task (for bug tasks linked to originals)
    parent_task: Mapped["Task | None"] = relationship(
        "Task", remote_side=[id], foreign_keys=[parent_task_id], backref="child_tasks"
    )

    # Self-referential M2M: tasks this task depends on
    depends_on: Mapped[list["Task"]] = relationship(
        "Task",
        secondary=task_dependencies,
        primaryjoin=id == task_dependencies.c.task_id,
        secondaryjoin=id == task_dependencies.c.dependency_id,
        backref="dependents",
    )


class TaskHistory(Base):
    __tablename__ = "task_history"
    __table_args__ = (Index("ix_task_history_task_timestamp", "task_id", "timestamp"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    from_status: Mapped[str] = mapped_column(String(50), nullable=False)
    to_status: Mapped[str] = mapped_column(String(50), nullable=False)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra_metadata: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    task: Mapped["Task"] = relationship("Task", back_populates="history")
