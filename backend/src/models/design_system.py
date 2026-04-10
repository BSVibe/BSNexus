"""Design system models — replaces the external Stitch MCP integration.

A DesignSystem is project-scoped and holds the tokens, components, and
patterns the Designer agent must use when generating new screens.
Screens reference their parent DesignSystem so generators can enforce
consistency without re-deriving styles each call.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.src.storage.database import Base


class DesignSystem(Base):
    """Per-project design system: tokens, components, patterns, brand."""

    __tablename__ = "design_systems"
    __table_args__ = (Index("ix_design_systems_project", "project_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="Default")

    # JSON blobs — schemaless on purpose so the Designer agent can extend
    # them as the design system matures without DB migrations.
    tokens: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")
    components: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")
    patterns: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")
    brand_voice: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    screens: Mapped[list["Screen"]] = relationship(
        "Screen", back_populates="design_system", cascade="all, delete-orphan"
    )


class Screen(Base):
    """A single UI screen — name, route, intent, JSON spec, and code path."""

    __tablename__ = "design_screens"
    __table_args__ = (
        Index("ix_design_screens_project", "project_id"),
        Index("ix_design_screens_design_system", "design_system_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    design_system_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("design_systems.id", ondelete="CASCADE"), nullable=False
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    route: Mapped[str | None] = mapped_column(String(500), nullable=True)
    intent: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Component tree the agent produced (e.g. JSON describing nodes).
    spec: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")
    # Path inside the project workspace where the generated React/Tailwind
    # source lives. Optional — preview mode does not require code on disk.
    generated_code_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    preview_image_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    design_system: Mapped["DesignSystem"] = relationship("DesignSystem", back_populates="screens")
