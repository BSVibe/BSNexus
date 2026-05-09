from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, JSON, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.src.core.domain import WorkPlanCreatedBy, WorkPlanStatus
from backend.src.storage.database import Base


class WorkPlan(Base):
    __tablename__ = "work_plans"
    __table_args__ = (Index("ix_work_plans_request_version", "request_id", "version", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("requests.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    steps: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_by: Mapped[WorkPlanCreatedBy] = mapped_column(
        Enum(WorkPlanCreatedBy, name="work_plan_created_by"), nullable=False
    )
    status: Mapped[WorkPlanStatus] = mapped_column(
        Enum(WorkPlanStatus, name="work_plan_status"), nullable=False, default=WorkPlanStatus.draft
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
