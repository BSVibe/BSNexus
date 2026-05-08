"""Deliverable + DeliverableVersion — user-facing outputs of a request.

A Deliverable is the logical thing the founder asked for (a feature, a
doc, a design). DeliverableVersions are the append-only physical
revisions stored via the appropriate backend:

- ``git``: code changes committed to a branch
- ``object``: files stored in S3-compatible object storage (MinIO/R2)
- ``url``: external resource reference
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.src.storage.database import Base


class DeliverableType(str, enum.Enum):
    code = "code"
    doc = "doc"
    design = "design"
    data = "data"
    url = "url"


class DeliverableStatus(str, enum.Enum):
    draft = "draft"
    ready = "ready"
    delivered = "delivered"


class StorageBackend(str, enum.Enum):
    git = "git"
    object = "object"
    url = "url"


class ProofState(str, enum.Enum):
    """States in the Deliverable proof-state machine.

    Disjoint from ``DeliverableStatus`` (which marks "the AI run produced
    this") and from ``RunStatus``. The proof state is the founder-visible
    "did this actually work" signal — only ``verified`` deliverables are
    presented as shipped in the UI.
    """

    verification_missing = "verification_missing"
    verifying = "verifying"
    verified = "verified"
    verification_failed = "verification_failed"
    human_review_required = "human_review_required"
    not_applicable = "not_applicable"


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

    type: Mapped[DeliverableType] = mapped_column(Enum(DeliverableType), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[DeliverableStatus] = mapped_column(
        Enum(DeliverableStatus), nullable=False, default=DeliverableStatus.draft
    )

    # Proof model (decision-locks A1, 2026-05-08).
    proof_state: Mapped[ProofState] = mapped_column(
        Enum(ProofState),
        nullable=False,
        default=ProofState.verification_missing,
        server_default=ProofState.verification_missing.value,
    )
    verifier_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verifier_inputs: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    verification_exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    proof_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    proof_refs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    risk_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("deliverable_versions.id", use_alter=True, ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    versions: Mapped[list["DeliverableVersion"]] = relationship(
        "DeliverableVersion",
        back_populates="deliverable",
        foreign_keys="DeliverableVersion.deliverable_id",
        cascade="all, delete-orphan",
    )


class DeliverableVersion(Base):
    """Append-only version of a Deliverable. Never UPDATE, never DELETE in
    normal flow.
    """

    __tablename__ = "deliverable_versions"
    __table_args__ = (Index("ix_deliverable_versions_deliverable", "deliverable_id", "version_int"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    deliverable_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("deliverables.id", ondelete="CASCADE"), nullable=False
    )
    version_int: Mapped[int] = mapped_column(Integer, nullable=False)

    storage_backend: Mapped[StorageBackend] = mapped_column(Enum(StorageBackend), nullable=False)
    content_ref: Mapped[dict] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_by_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("execution_runs.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    deliverable: Mapped["Deliverable"] = relationship(
        "Deliverable",
        back_populates="versions",
        foreign_keys=[deliverable_id],
    )
