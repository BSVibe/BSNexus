"""ProjectChannel model — maps a project to an external chat channel.

Phase 8 in the overhaul roadmap. The Slack adapter (and any future
Discord / Teams adapters) will tail the per-project chat events stream
and post messages to the configured channel.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.src.storage.database import Base


class ProjectChannel(Base):
    __tablename__ = "project_channels"
    __table_args__ = (
        Index("ix_project_channels_project_kind", "project_id", "kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # "slack", "discord", ...
    external_channel_id: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Encrypted bot token / webhook URL — secrets are persisted as text
    # because the encryption layer wraps them before storage.
    credentials_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
