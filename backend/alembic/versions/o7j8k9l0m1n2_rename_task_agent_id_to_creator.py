"""rename tasks.agent_id to creator_agent_id

Separates the creator identity (who created the task) from the executor
(assigned_agent_id). Previously global_dispatcher.py:359 was overwriting
agent_id with the executor, corrupting audit data.

Revision ID: o7j8k9l0m1n2
Revises: n6i7j8k9l0m1
Create Date: 2026-04-18 21:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "o7j8k9l0m1n2"
down_revision: Union[str, None] = "n6i7j8k9l0m1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("tasks", "agent_id", new_column_name="creator_agent_id")


def downgrade() -> None:
    op.alter_column("tasks", "creator_agent_id", new_column_name="agent_id")
