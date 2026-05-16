from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, JSON, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.src.core.domain import RunAttemptPhase, RunAttemptStatus
from backend.src.storage.database import Base


class RunAttempt(Base):
    __tablename__ = "run_attempts"
    __table_args__ = (Index("ix_run_attempts_work_step", "work_step_id", "started_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    work_step_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("work_steps.id", ondelete="CASCADE"), nullable=False
    )
    executor_kind: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    phase: Mapped[RunAttemptPhase] = mapped_column(
        Enum(RunAttemptPhase, name="run_attempt_phase"), nullable=False, default=RunAttemptPhase.prepare
    )
    status: Mapped[RunAttemptStatus] = mapped_column(
        Enum(RunAttemptStatus, name="run_attempt_status"), nullable=False, default=RunAttemptStatus.running
    )
    round_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    terminal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    telemetry: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Tier 1 continuation: when this RunAttempt exhausts its work-round
    # budget with real progress, a model-independent handoff record is
    # written here (summary / files_touched / verification_state /
    # remaining / blockers). The next RunAttempt for the same WorkStep
    # is seeded from it — never from this attempt's LLM message history,
    # so a different model (BSGateway routing) can pick the work up.
    handoff: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ToolEvent(Base):
    __tablename__ = "tool_events"
    __table_args__ = (Index("ix_tool_events_run_round", "run_attempt_id", "round_index"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_attempt_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("run_attempts.id", ondelete="CASCADE"), nullable=False
    )
    round_index: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_name: Mapped[str] = mapped_column(Text, nullable=False)
    args_hash: Mapped[str] = mapped_column(Text, nullable=False)
    args_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    writes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
