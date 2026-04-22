"""Request model — user-facing unit of work.

A Request is what the user (acting as founder) directs the company to do.
It's derived from conversation messages by the RequestExtractor.

Requests fan out into one or more ExecutionRuns internally. The user sees
requests and their resulting deliverables; runs stay inside the Inside
panel.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.src.storage.database import Base


class RequestStatus(str, enum.Enum):
    open = "open"
    running = "running"
    completed = "completed"
    abandoned = "abandoned"


class Request(Base):
    __tablename__ = "requests"
    __table_args__ = (
        Index("ix_requests_project_status", "project_id", "status"),
        Index("ix_requests_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )

    origin_message_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("conversation_messages.id", ondelete="SET NULL"),
        nullable=True,
    )

    intent_summary: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[RequestStatus] = mapped_column(
        Enum(RequestStatus), nullable=False, default=RequestStatus.open
    )
    user_confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("requests.id", ondelete="SET NULL"), nullable=True
    )

    composition_root_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("composition_snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
