"""Remote worker model — self-hosted runner registration and heartbeat."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from backend.src.storage.database import Base


class Worker(Base):
    __tablename__ = "workers"
    __table_args__ = (
        Index("ix_workers_tenant", "tenant_id"),
        Index("ix_workers_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    labels: Mapped[list] = mapped_column(JSON, nullable=False, default=list, server_default="[]")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="offline", server_default="offline")
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    capabilities: Mapped[list] = mapped_column(JSON, nullable=False, default=list, server_default="[]")
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
