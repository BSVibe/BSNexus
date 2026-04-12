"""PlanProposal — proposed Phase/Task that awaits approval before creation.

When an agent emits ``[CREATE_PHASE]`` or ``[CREATE_TASK]`` markers and the
project's approval settings require review, the marker is stored as a
proposal instead of being immediately applied. Proposals can be approved
by a plan-capability agent (auto) or by the user (manual).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, JSON, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.src.storage.database import Base


class ProposalType(str, enum.Enum):
    phase = "phase"
    task = "task"


class ProposalStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class PlanProposal(Base):
    __tablename__ = "plan_proposals"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    # The agent that proposed this Phase/Task.
    proposer_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    proposer_agent_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    proposal_type: Mapped[ProposalType] = mapped_column(
        Enum(ProposalType, name="proposaltype", create_constraint=False), nullable=False
    )
    # The original JSON payload from the marker.
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)

    status: Mapped[ProposalStatus] = mapped_column(
        Enum(ProposalStatus, name="proposalstatus", create_constraint=False),
        default=ProposalStatus.pending,
    )
    # Who approved/rejected — null means user (human).
    reviewer_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    review_comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
