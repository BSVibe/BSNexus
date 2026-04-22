"""Cost tracking model for per-tenant budget reporting.

Costs are accumulated per ExecutionRun. Per-agent budgeting (the old
org-chart concept) is gone with the agents table; BSGateway will own
authoritative cost data once the integration stabilizes.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.src.storage.database import Base


class CostRecord(Base):
    __tablename__ = "cost_records"
    __table_args__ = (
        Index("ix_cost_records_tenant", "tenant_id"),
        Index("ix_cost_records_tenant_recorded", "tenant_id", "recorded_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    execution_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("execution_runs.id", ondelete="SET NULL"), nullable=True
    )
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
