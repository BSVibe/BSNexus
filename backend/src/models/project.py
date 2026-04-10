"""Project and workspace models."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Integer, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.src.storage.database import Base


class WorkspaceType(str, enum.Enum):
    server_managed = "server_managed"
    local_import = "local_import"
    github_connected = "github_connected"


class ProjectStatus(str, enum.Enum):
    design = "design"
    active = "active"
    paused = "paused"
    completed = "completed"


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
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

    status: Mapped[ProjectStatus] = mapped_column(Enum(ProjectStatus), nullable=False, default=ProjectStatus.design)
    max_concurrent_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    llm_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    phases: Mapped[list["Phase"]] = relationship(  # noqa: F821
        "Phase", back_populates="project", cascade="all, delete-orphan"
    )
