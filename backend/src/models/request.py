from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.src.core.domain import RequestStatus
from backend.src.storage.database import Base


class Request(Base):
    __tablename__ = "requests"
    __table_args__ = (
        Index("ix_requests_project_status", "project_id", "status"),
        Index("ix_requests_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    origin_direction_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("directions.id", ondelete="SET NULL"), nullable=True
    )
    intent: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[RequestStatus] = mapped_column(
        Enum(RequestStatus, name="request_status"), nullable=False, default=RequestStatus.open
    )
    current_step_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    last_brief_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    # G8.3 — set when the Request ships and a GitHub PR is opened from
    # ``bsnexus/req-<id>`` against the project's base branch. Both null
    # when the project has no repo binding or the PR creation soft-failed
    # (transition_request does NOT revert ``shipped`` on PR failure).
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pr_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
