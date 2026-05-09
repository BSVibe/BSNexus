from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, JSON, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.src.core.domain import BriefScope
from backend.src.storage.database import Base


class BriefSnapshot(Base):
    __tablename__ = "brief_snapshots"
    __table_args__ = (Index("ix_brief_snapshots_scope_generated", "tenant_id", "scope", "generated_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    scope: Mapped[BriefScope] = mapped_column(Enum(BriefScope, name="brief_scope"), nullable=False)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    request_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("requests.id", ondelete="CASCADE"), nullable=True
    )
    sections: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
