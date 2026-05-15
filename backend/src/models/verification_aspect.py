"""``VerificationAspect`` — first-class row per (deliverable, aspect) pair.

The multi-aspect verifier model. A deliverable's overall
``proof_state`` is derived from the statuses of its aspects; each
aspect represents a discrete verification dimension (test, lint,
install smoke, future: build, external audit, knowledge check,
marketing copy fact-check, ...). Adding a new aspect is additive —
no rework of the roll-up or downstream consumers.

Replaces the single-row-per-deliverable ``ProofAttempt`` model. The
``proof:queue`` worker spawns the applicable aspects per message,
runs each, then rolls up to ``Deliverable.proof_state``. Per-aspect
queue topology can be introduced later without schema change (this
is the Phase 1 of the C-shape rollout — abstraction first, queue
split when SLA requires it).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, JSON, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.src.core.domain import ProofAspectStatus, ProofAspectType
from backend.src.storage.database import Base


class VerificationAspect(Base):
    __tablename__ = "verification_aspects"
    __table_args__ = (
        Index("ix_verification_aspects_deliverable", "deliverable_id", "created_at"),
        Index("ix_verification_aspects_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    deliverable_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("deliverables.id", ondelete="CASCADE"), nullable=False
    )
    aspect_type: Mapped[ProofAspectType] = mapped_column(
        Enum(ProofAspectType, name="proof_aspect_type"), nullable=False
    )
    status: Mapped[ProofAspectStatus] = mapped_column(
        Enum(ProofAspectStatus, name="proof_aspect_status"),
        nullable=False,
        default=ProofAspectStatus.queued,
    )
    # The aspect's input contract: the command(s) the runner executed,
    # the workspace refs it expected, the timeout. Stored as JSON so
    # different aspect_types can carry different shapes without schema
    # gymnastics. Today every aspect carries ``{commands: [[...], ...],
    # required_refs: [...], timeout_s: int}``.
    inputs: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # ``blocking`` controls roll-up — a blocking aspect that ``failed``
    # blocks the deliverable from ``verified``. Non-blocking aspects
    # (future: optional security audit) can fail without blocking.
    blocking: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # ``attempt_no`` tracks retries of the same (deliverable, aspect):
    # operator-triggered re-verify increments. Surfaces in the Inside
    # panel as "lint #2 of 3".
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
