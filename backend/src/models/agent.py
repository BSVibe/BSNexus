"""Agent model — dynamically configurable AI agent with org chart hierarchy."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.src.storage.database import Base


class Agent(Base):
    __tablename__ = "agents"
    __table_args__ = (
        Index("ix_agents_tenant", "tenant_id"),
        Index("ix_agents_tenant_role", "tenant_id", "role"),
        Index("ix_agents_parent", "parent_agent_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    # Identity — all free-form, user-editable
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    job_description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Executor binding — NULL means "use tenant default"
    executor_config_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("executor_configs.id", ondelete="SET NULL"), nullable=True
    )
    executor_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="claude_api", server_default="claude_api"
    )
    executor_config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    skills: Mapped[list | None] = mapped_column(JSON, nullable=True)
    capabilities: Mapped[list] = mapped_column(JSON, nullable=False, default=list, server_default="[]")

    # Org chart — self-referential, free hierarchy (no depth limit)
    parent_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )

    # Heartbeat
    heartbeat_interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    heartbeat_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Budget (cents to avoid float — MUST use Decimal in business logic)
    monthly_budget_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_month_spent_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    # Status
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="offline", server_default="offline")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    parent_agent: Mapped["Agent | None"] = relationship(
        "Agent", remote_side=[id], foreign_keys=[parent_agent_id], backref="child_agents"
    )
