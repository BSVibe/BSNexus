"""Per-tenant integration settings.

Each row configures one sibling-service hook (BSage knowledge,
BSupervisor audit) for one tenant. Encrypted api_key at rest; never log
raw.

BSGateway is NOT modeled as an integration here — it's an executor
kind. A tenant points runs through BSGateway by registering an
``ExecutorConfig`` with ``executor_type="bsgateway"`` and marking it
``is_selected``. That keeps the "who runs the LLM call" decision in one
place (Executors tab) and leaves Integrations for non-executor hooks.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from backend.src.storage.database import Base


class IntegrationProvider(str, enum.Enum):
    bsage = "bsage"
    bsupervisor = "bsupervisor"


class TenantIntegrationConfig(Base):
    __tablename__ = "tenant_integration_configs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", name="uq_tenant_integration_provider"),
        Index("ix_tenant_integration_configs_tenant", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[IntegrationProvider] = mapped_column(
        Enum(IntegrationProvider), nullable=False
    )

    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    extra_config: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
