"""CompositionSnapshot — immutable record of how a run's prompt was built.

BSNexus owns prompt assembly; BSage provides search primitives. When a run
is dispatched, the PromptAssembler pulls knowledge fragments via the
KnowledgeClient and composes a system prompt. That composition is frozen
as a snapshot for audit, debugging, and the Inside panel.

Snapshots are append-only. A new composition (even for the same request)
creates a new row.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Index, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from backend.src.storage.database import Base


class CompositionSource(str, enum.Enum):
    """Where the composition was assembled from.

    - ``bsage``: BSage search returned knowledge fragments used for assembly.
    - ``local``: BSage unavailable or disabled — composition used only local
      templates. Surfaced in the Inside panel so the user knows the run
      executed in degraded knowledge mode.
    """

    bsage = "bsage"
    local = "local"


class CompositionSnapshot(Base):
    __tablename__ = "composition_snapshots"
    __table_args__ = (
        Index("ix_composition_snapshots_request", "request_id"),
        Index("ix_composition_snapshots_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    request_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("requests.id", ondelete="CASCADE"), nullable=False
    )
    execution_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("execution_runs.id", ondelete="SET NULL"), nullable=True
    )

    source: Mapped[CompositionSource] = mapped_column(
        Enum(CompositionSource), nullable=False, default=CompositionSource.local
    )

    bsage_composition_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    bsage_etag: Mapped[str | None] = mapped_column(String(255), nullable=True)

    system_prompt_ref: Mapped[dict] = mapped_column(JSON, nullable=False)
    tools_allowed: Mapped[list] = mapped_column(JSON, nullable=False, default=list, server_default="[]")
    context_doc_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list, server_default="[]")

    persona_label: Mapped[str] = mapped_column(String(255), nullable=False)
    fit_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
