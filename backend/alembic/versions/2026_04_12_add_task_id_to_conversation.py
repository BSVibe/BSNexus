"""add task_id to conversation_messages

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-04-12 12:00:00.000000

Links chat messages to tasks, enabling task-scoped chat views.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversation_messages",
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_conversation_messages_task", "conversation_messages", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_conversation_messages_task", table_name="conversation_messages")
    op.drop_column("conversation_messages", "task_id")
