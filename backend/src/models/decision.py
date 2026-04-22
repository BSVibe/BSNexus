"""Decision — items requiring founder approval.

The Decisions inbox surface shows open decisions. Blocking decisions halt
the associated request until resolved. Non-blocking decisions are
informational (log what the company decided on the founder's behalf).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from backend.src.storage.database import Base


class Decision(Base):
    __tablename__ = "decisions"
    __table_args__ = (
        Index("ix_decisions_project_resolved", "project_id", "resolved_at"),
        Index("ix_decisions_tenant_blocking", "tenant_id", "blocking", "resolved_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    request_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("requests.id", ondelete="SET NULL"), nullable=True
    )
    origin_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("execution_runs.id", ondelete="SET NULL"), nullable=True
    )

    question: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[list] = mapped_column(JSON, nullable=False, default=list, server_default="[]")
    blocking: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
