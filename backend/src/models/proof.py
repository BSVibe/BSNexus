from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, JSON, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.src.core.domain import DeliverableType, ProofAttemptStatus
from backend.src.storage.database import Base


class ProofPolicy(Base):
    __tablename__ = "proof_policies"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    deliverable_type: Mapped[DeliverableType] = mapped_column(
        Enum(DeliverableType, name="proof_policy_deliverable_type"), nullable=False
    )
    verifier_type: Mapped[str] = mapped_column(Text, nullable=False)
    command_template: Mapped[list | None] = mapped_column(JSON, nullable=True)
    required_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    timeout_s: Mapped[int] = mapped_column(Integer, nullable=False)
    pass_condition: Mapped[str] = mapped_column(Text, nullable=False)


class ProofAttempt(Base):
    __tablename__ = "proof_attempts"
    __table_args__ = (Index("ix_proof_attempts_deliverable_created", "deliverable_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    deliverable_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("deliverables.id", ondelete="CASCADE"), nullable=False
    )
    verifier_type: Mapped[str] = mapped_column(Text, nullable=False)
    inputs: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[ProofAttemptStatus] = mapped_column(
        Enum(ProofAttemptStatus, name="proof_attempt_status"),
        nullable=False,
        default=ProofAttemptStatus.queued,
    )
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    proof_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    proof_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
