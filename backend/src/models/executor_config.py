"""Per-tenant executor (LLM dispatch) configuration.

CLAUDE.md MUST rule "two-path LLM dispatch":

  - ``executor_type=bsgateway`` → BSGateway worker pool
    (``base_url`` = gateway URL, ``api_key`` = registration token).
  - ``executor_type=llm_api`` → litellm direct
    (``base_url`` = provider host, ``model`` = litellm model id, ``api_key`` = provider key).

One config row per tenant — switching paths is a kind change, not a
new row. ``api_key_encrypted`` is the only secret; the wire shape
exposes ``has_api_key`` only.
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


class ExecutorKind(str, enum.Enum):
    bsgateway = "bsgateway"
    llm_api = "llm_api"


class ExecutorConfig(Base):
    __tablename__ = "executor_configs"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_executor_config_tenant"),
        Index("ix_executor_configs_tenant", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[ExecutorKind] = mapped_column(Enum(ExecutorKind, name="executor_kind"), nullable=False)

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # llm_api only — litellm model id, e.g. ``ollama_chat/qwen3-coder:30b``
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # bsgateway: registration token; llm_api: provider api key.
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    extra_config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
