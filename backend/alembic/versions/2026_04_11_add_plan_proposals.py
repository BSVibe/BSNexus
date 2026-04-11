"""add plan_proposals table

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f7
Create Date: 2026-04-11 18:00:00.000000

PlanProposal stores proposed Phase/Task creations that await approval.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | Sequence[str] | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "plan_proposals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("proposer_agent_id", sa.Uuid(), sa.ForeignKey("agents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("proposer_agent_name", sa.String(255), nullable=True),
        sa.Column("proposal_type", sa.String(10), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(10), server_default="pending", nullable=False),
        sa.Column("reviewer_agent_id", sa.Uuid(), sa.ForeignKey("agents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("review_comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_plan_proposals_project_status",
        "plan_proposals",
        ["project_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_plan_proposals_project_status", table_name="plan_proposals")
    op.drop_table("plan_proposals")
