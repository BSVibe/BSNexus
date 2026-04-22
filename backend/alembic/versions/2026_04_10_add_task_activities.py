"""add task_activities table

Revision ID: b7c8d9e0f1a2
Revises: 9a8b7c6d5e4f
Create Date: 2026-04-10 12:30:00.000000

Adds the structured event log that powers the Plan view detail panel.
Two granularity levels live in the same table:

  level=milestone -> task started/completed/failed/progress note
  level=tool      -> raw tool calls (read file, exec, write, llm call)

The detail panel shows milestones by default and reveals tool entries
behind a toggle.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7c8d9e0f1a2"
down_revision: str | Sequence[str] | None = "9a8b7c6d5e4f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "task_activities",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_id", sa.Uuid(), sa.ForeignKey("agents.id", ondelete="SET NULL"), nullable=True),
        sa.Column(
            "level",
            sa.Enum("milestone", "tool", name="activitylevel"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_task_activities_task_created", "task_activities", ["task_id", "created_at"]
    )
    op.create_index(
        "ix_task_activities_project_created", "task_activities", ["project_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_task_activities_project_created", table_name="task_activities")
    op.drop_index("ix_task_activities_task_created", table_name="task_activities")
    op.drop_table("task_activities")
    op.execute("DROP TYPE IF EXISTS activitylevel")
