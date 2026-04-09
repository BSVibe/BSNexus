"""ExecutorConfig model — registered executor instances with per-type configuration."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from backend.src.storage.database import Base


class ExecutorConfig(Base):
    """A registered executor instance.

    Each row represents a configured executor that agents can use.
    For example, a tenant might register:
    - "Claude Code (local)" with executor_type=claude_code, config={execution_mode: "self_hosted"}
    - "Claude Code (cloud)" with executor_type=claude_code, config={execution_mode: "tenant"}
    - "BSGateway Prod" with executor_type=bsgateway, config={bsgateway_url: "...", bsgateway_api_key: "..."}
    """

    __tablename__ = "executor_configs"
    __table_args__ = (
        Index("ix_executor_configs_tenant", "tenant_id"),
        Index(
            "uq_executor_configs_one_default_per_tenant",
            "tenant_id",
            unique=True,
            postgresql_where=text("is_default = true"),
            sqlite_where=text("is_default = 1"),
        ),
        CheckConstraint("length(name) > 0", name="ck_executor_configs_name_not_empty"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    executor_type: Mapped[str] = mapped_column(String(50), nullable=False)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_default: Mapped[bool] = mapped_column(default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
