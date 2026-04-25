"""Project and workspace models."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from backend.src.storage.database import Base


class WorkspaceType(str, enum.Enum):
    server_managed = "server_managed"
    local_import = "local_import"
    github_connected = "github_connected"


class ProjectStatus(str, enum.Enum):
    # Single operational state today — no code transitions projects out
    # of ``active``. ``archived`` is reserved for a future user-initiated
    # "hide from dashboard" action (delete is the hard-remove path).
    active = "active"
    archived = "archived"


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (Index("ix_projects_tenant", "tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    design_doc_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    repo_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Workspace management
    workspace_type: Mapped[WorkspaceType] = mapped_column(
        Enum(WorkspaceType), nullable=False, default=WorkspaceType.server_managed, server_default="server_managed"
    )
    workspace_dir: Mapped[str | None] = mapped_column(String(500), nullable=True)
    github_repo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    github_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    github_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[ProjectStatus] = mapped_column(
        Enum(ProjectStatus), nullable=False, default=ProjectStatus.active, server_default="active"
    )
    max_concurrent_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    llm_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Optional references to company-OS siblings (resolved per tenant config).
    bsage_workspace_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    bsupervisor_policy_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
