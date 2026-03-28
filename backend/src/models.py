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


# ── Enums ──────────────────────────────────────────────────────────────


class ProjectStatus(str, enum.Enum):
    design = "design"
    active = "active"
    paused = "paused"
    completed = "completed"


class PhaseStatus(str, enum.Enum):
    pending = "pending"
    active = "active"
    completed = "completed"


class TaskStatus(str, enum.Enum):
    waiting = "waiting"
    ready = "ready"
    in_progress = "in_progress"
    review = "review"
    done = "done"
    redesign = "redesign"


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
    architect = "architect"
    auto_bug = "auto_bug"
    manual = "manual"


class DesignSessionStatus(str, enum.Enum):
    active = "active"
    project_bound = "project_bound"
    cancelled = "cancelled"


class MessageRole(str, enum.Enum):
    user = "user"
    assistant = "assistant"


class MessageType(str, enum.Enum):
    chat = "chat"
    internal = "internal"


# ── Association Table ──────────────────────────────────────────────────

task_dependencies = Table(
    "task_dependencies",
    Base.metadata,
    Column("task_id", Uuid, ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True),
    Column("dependency_id", Uuid, ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True),
)


# ── Models ─────────────────────────────────────────────────────────────


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    design_doc_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    repo_path: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[ProjectStatus] = mapped_column(Enum(ProjectStatus), nullable=False, default=ProjectStatus.design)
    llm_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    phases: Mapped[list["Phase"]] = relationship("Phase", back_populates="project", cascade="all, delete-orphan")
    design_sessions: Mapped[list["DesignSession"]] = relationship(
        "DesignSession", back_populates="project", cascade="all, delete-orphan"
    )


class Phase(Base):
    __tablename__ = "phases"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    branch_name: Mapped[str] = mapped_column(String(255), nullable=False)
    order: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[PhaseStatus] = mapped_column(Enum(PhaseStatus), nullable=False, default=PhaseStatus.pending)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    project: Mapped["Project"] = relationship("Project", back_populates="phases")
    tasks: Mapped[list["Task"]] = relationship("Task", back_populates="phase", cascade="all, delete-orphan")


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
    status: Mapped[TaskStatus] = mapped_column(Enum(TaskStatus), nullable=False, default=TaskStatus.waiting)
    priority: Mapped[TaskPriority] = mapped_column(Enum(TaskPriority), nullable=False, default=TaskPriority.medium)
    task_type: Mapped[TaskType] = mapped_column(Enum(TaskType), nullable=False, default=TaskType.feature)
    source: Mapped[TaskSource] = mapped_column(Enum(TaskSource), nullable=False, default=TaskSource.architect)
    parent_task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    worker_prompt: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    qa_prompt: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    branch_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    commit_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    qa_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    executor_type: Mapped[str] = mapped_column(String(50), nullable=False, default="coding", server_default="coding")
    executor_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")
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
    project: Mapped["Project"] = relationship("Project")
    phase: Mapped["Phase"] = relationship("Phase", back_populates="tasks")
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


class DesignSession(Base):
    __tablename__ = "design_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    status: Mapped[DesignSessionStatus] = mapped_column(
        Enum(DesignSessionStatus), nullable=False, default=DesignSessionStatus.active
    )
    llm_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    project: Mapped["Project | None"] = relationship("Project", back_populates="design_sessions")
    messages: Mapped[list["DesignMessage"]] = relationship(
        "DesignMessage", back_populates="session", cascade="all, delete-orphan"
    )


class DesignMessage(Base):
    __tablename__ = "design_messages"
    __table_args__ = (Index("ix_design_messages_session_created", "session_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("design_sessions.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[MessageRole] = mapped_column(Enum(MessageRole), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    message_type: Mapped[MessageType] = mapped_column(
        Enum(MessageType), nullable=False, default=MessageType.chat, server_default="chat"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    session: Mapped["DesignSession"] = relationship("DesignSession", back_populates="messages")


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
