"""add task_suggestions table

Revision ID: m5h6i7j8k9l0
Revises: l4g5h6i7j8k9
Create Date: 2026-03-28 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "m5h6i7j8k9l0"
down_revision: Union[str, None] = "l4g5h6i7j8k9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

suggestion_status_enum = sa.Enum("pending", "approved", "rejected", "modified", name="suggestionstatus")


def upgrade() -> None:
    conn = op.get_bind()
    suggestion_status_enum.create(conn, checkfirst=True)
    op.create_table(
        "task_suggestions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("task_type", sa.String(50), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("estimated_effort", sa.String(50), nullable=True),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("pending", "approved", "rejected", "modified", name="suggestionstatus", create_type=False),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_task_suggestions_project_status", "task_suggestions", ["project_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_task_suggestions_project_status", table_name="task_suggestions")
    op.drop_table("task_suggestions")
    conn = op.get_bind()
    suggestion_status_enum.drop(conn, checkfirst=True)
