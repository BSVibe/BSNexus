"""ExecutionRun — internal unit of work (replaces Task).

An ExecutionRun is the internal decomposition of a Request. Users never
see runs directly; the Inside panel exposes them for debugging/trust.

Each run is dispatched through the RunOrchestrator:
  composer.compose → executor.execute (BSGateway absorbs the
  BSupervisor run.pre / run.post audit calls via its LiteLLM hook —
  Lockin §Architectural shifts #1, P0.7).

Lifecycle states mirror the original Task model:
  pending → running → (blocked ↔ pending) → done
"""

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


class RunStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    blocked = "blocked"
    done = "done"


class RunPriority(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


# Association: a run depends on other runs.
execution_run_dependencies = Table(
    "execution_run_dependencies",
    Base.metadata,
    Column(
        "run_id",
        Uuid,
        ForeignKey("execution_runs.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "dependency_id",
        Uuid,
        ForeignKey("execution_runs.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class ExecutionRun(Base):
    __tablename__ = "execution_runs"
    __table_args__ = (
        Index("ix_execution_runs_project_status", "project_id", "status"),
        Index("ix_execution_runs_request", "request_id"),
        Index("ix_execution_runs_tenant_created", "tenant_id", "created_at"),
        # S2-1 M1 (revised after founder-metaphor refactor): the original
        # finding cited assigned_agent_id (now retired) and created_at
        # (already covered above). The remaining gap is parent_run_id —
        # the iterative replanner queries by it on every completed run.
        Index("ix_execution_runs_parent_run", "parent_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    request_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("requests.id", ondelete="CASCADE"), nullable=False)
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("execution_runs.id", ondelete="SET NULL"), nullable=True
    )
    composition_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey(
            "composition_snapshots.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_execution_runs_composition_snapshot",
        ),
        nullable=True,
    )

    status: Mapped[RunStatus] = mapped_column(Enum(RunStatus), nullable=False, default=RunStatus.pending)
    priority: Mapped[RunPriority] = mapped_column(Enum(RunPriority), nullable=False, default=RunPriority.medium)

    # Per-run user direction. For the founder's original message it
    # mirrors ``request.intent_summary``; for planner-seeded child runs
    # it carries the phase-specific prompt.
    directive: Mapped[str | None] = mapped_column(Text, nullable=True)

    output_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_ref: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Failure-mode aggregate populated at run terminal transition (PR7).
    # Schema mirror lives in ``backend/src/schemas/run_summary.py``.
    # Nullable — pre-existing rows stay NULL; the instrumentation
    # path stamps it on every new run finalization.
    run_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    estimated_cost_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    actual_cost_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    branch_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    commit_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    history: Mapped[list["ExecutionRunHistory"]] = relationship(
        "ExecutionRunHistory",
        back_populates="run",
        cascade="all, delete-orphan",
    )

    parent_run: Mapped["ExecutionRun | None"] = relationship(
        "ExecutionRun",
        remote_side=[id],
        foreign_keys=[parent_run_id],
        backref="child_runs",
    )

    depends_on: Mapped[list["ExecutionRun"]] = relationship(
        "ExecutionRun",
        secondary=execution_run_dependencies,
        primaryjoin=id == execution_run_dependencies.c.run_id,
        secondaryjoin=id == execution_run_dependencies.c.dependency_id,
        backref="dependents",
    )


class ExecutionRunHistory(Base):
    __tablename__ = "execution_run_history"
    __table_args__ = (Index("ix_execution_run_history_run_timestamp", "run_id", "timestamp"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("execution_runs.id", ondelete="CASCADE"), nullable=False)
    from_status: Mapped[RunStatus] = mapped_column(Enum(RunStatus), nullable=False)
    to_status: Mapped[RunStatus] = mapped_column(Enum(RunStatus), nullable=False)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra_metadata: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    run: Mapped["ExecutionRun"] = relationship("ExecutionRun", back_populates="history")
