from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.src.core.domain import DeliverableStatus, DeliverableType, ProofState
from backend.src.storage.database import Base


class Deliverable(Base):
    __tablename__ = "deliverables"
    __table_args__ = (
        Index("ix_deliverables_project_status", "project_id", "status"),
        Index("ix_deliverables_request", "request_id"),
        Index("ix_deliverables_project_proof_state", "project_id", "proof_state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    request_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("requests.id", ondelete="SET NULL"), nullable=True
    )
    work_step_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("work_steps.id", ondelete="SET NULL"), nullable=True
    )

    type: Mapped[DeliverableType] = mapped_column(Enum(DeliverableType, name="deliverable_type"), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[DeliverableStatus] = mapped_column(
        Enum(DeliverableStatus, name="deliverable_status"), nullable=False, default=DeliverableStatus.draft
    )
    proof_state: Mapped[ProofState] = mapped_column(
        Enum(ProofState, name="proof_state"),
        nullable=False,
        default=ProofState.verification_missing,
        server_default=ProofState.verification_missing.value,
    )
    proof_policy_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("proof_policies.id", ondelete="SET NULL"), nullable=True
    )
    risk_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # G8.2 — populated by the VerifierWorker when a verified deliverable's
    # artifacts are committed to the request's repo branch. Null when the
    # project has no repo binding or a commit attempt failed; the next
    # verify retries cleanly because the absence is meaningful.
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
