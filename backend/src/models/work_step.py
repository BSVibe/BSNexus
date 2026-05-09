from __future__ import annotations

import uuid

from sqlalchemy import Enum, ForeignKey, Index, Integer, JSON, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from backend.src.core.domain import WorkStepStatus
from backend.src.storage.database import Base


class WorkStep(Base):
    __tablename__ = "work_steps"
    __table_args__ = (Index("ix_work_steps_request_status", "request_id", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("requests.id", ondelete="CASCADE"), nullable=False)
    plan_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("work_plans.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    expected_outputs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    verifier_policy: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[WorkStepStatus] = mapped_column(
        Enum(WorkStepStatus, name="work_step_status"), nullable=False, default=WorkStepStatus.pending
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
