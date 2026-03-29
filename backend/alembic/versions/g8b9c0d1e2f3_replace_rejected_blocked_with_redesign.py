"""replace rejected/blocked with redesign, add retry columns

Revision ID: g8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-02-18 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "g8b9c0d1e2f3"
down_revision: Union[str, None] = "f7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Convert columns to VARCHAR first to allow free updates
    op.execute("ALTER TABLE tasks ALTER COLUMN status TYPE VARCHAR(20)")
    op.execute("ALTER TABLE task_history ALTER COLUMN from_status TYPE VARCHAR(50)")
    op.execute("ALTER TABLE task_history ALTER COLUMN to_status TYPE VARCHAR(50)")

    # 2. Migrate rejected/blocked data
    op.execute("UPDATE tasks SET status = 'ready' WHERE status = 'rejected'")
    op.execute("UPDATE tasks SET status = 'waiting' WHERE status = 'blocked'")
    op.execute("UPDATE task_history SET to_status = 'ready' WHERE to_status = 'rejected'")
    op.execute("UPDATE task_history SET to_status = 'waiting' WHERE to_status = 'blocked'")
    op.execute("UPDATE task_history SET from_status = 'ready' WHERE from_status = 'rejected'")
    op.execute("UPDATE task_history SET from_status = 'waiting' WHERE from_status = 'blocked'")

    # 3. Recreate enum without rejected/blocked, with redesign
    op.execute("DROP TYPE taskstatus")
    op.execute(
        "CREATE TYPE taskstatus AS ENUM ('waiting', 'ready', 'queued', 'in_progress', 'review', 'done', 'redesign')"
    )
    op.execute("ALTER TABLE tasks ALTER COLUMN status TYPE taskstatus USING status::taskstatus")
    op.execute("ALTER TABLE task_history ALTER COLUMN from_status TYPE taskstatus USING from_status::taskstatus")
    op.execute("ALTER TABLE task_history ALTER COLUMN to_status TYPE taskstatus USING to_status::taskstatus")

    # 4. Add retry columns to tasks
    op.add_column("tasks", sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("tasks", sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"))
    op.add_column("tasks", sa.Column("qa_feedback_history", sa.JSON(), nullable=True))


def downgrade() -> None:
    # Remove retry columns
    op.drop_column("tasks", "qa_feedback_history")
    op.drop_column("tasks", "max_retries")
    op.drop_column("tasks", "retry_count")

    # Recreate enum with rejected/blocked
    op.execute("ALTER TABLE tasks ALTER COLUMN status TYPE VARCHAR(20)")
    op.execute("ALTER TABLE task_history ALTER COLUMN from_status TYPE VARCHAR(50)")
    op.execute("ALTER TABLE task_history ALTER COLUMN to_status TYPE VARCHAR(50)")
    op.execute("DROP TYPE taskstatus")
    op.execute(
        "CREATE TYPE taskstatus AS ENUM "
        "('waiting', 'ready', 'queued', 'in_progress', 'review', 'done', 'rejected', 'blocked')"
    )
    op.execute("ALTER TABLE tasks ALTER COLUMN status TYPE taskstatus USING status::taskstatus")
    op.execute("ALTER TABLE task_history ALTER COLUMN from_status TYPE taskstatus USING from_status::taskstatus")
    op.execute("ALTER TABLE task_history ALTER COLUMN to_status TYPE taskstatus USING to_status::taskstatus")
