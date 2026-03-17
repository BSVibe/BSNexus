"""add blocked status to taskstatus enum

Revision ID: a1b2c3d4e5f6
Revises: ed4a4a1b1581
Create Date: 2026-02-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "ed4a4a1b1581"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Recreate enum with 'blocked' to avoid transactional ADD VALUE issues
    op.execute("ALTER TABLE tasks ALTER COLUMN status TYPE VARCHAR(20)")
    op.execute("ALTER TABLE task_history ALTER COLUMN from_status TYPE VARCHAR(50)")
    op.execute("ALTER TABLE task_history ALTER COLUMN to_status TYPE VARCHAR(50)")
    op.execute("DROP TYPE IF EXISTS taskstatus")
    op.execute(
        "CREATE TYPE taskstatus AS ENUM "
        "('waiting', 'ready', 'queued', 'in_progress', 'review', 'done', 'rejected', 'blocked')"
    )
    op.execute("ALTER TABLE tasks ALTER COLUMN status TYPE taskstatus USING status::taskstatus")
    op.execute("ALTER TABLE task_history ALTER COLUMN from_status TYPE taskstatus USING from_status::taskstatus")
    op.execute("ALTER TABLE task_history ALTER COLUMN to_status TYPE taskstatus USING to_status::taskstatus")


def downgrade() -> None:
    # Remove blocked from enum
    op.execute("ALTER TABLE tasks ALTER COLUMN status TYPE VARCHAR(20)")
    op.execute("ALTER TABLE task_history ALTER COLUMN from_status TYPE VARCHAR(50)")
    op.execute("ALTER TABLE task_history ALTER COLUMN to_status TYPE VARCHAR(50)")
    op.execute("DROP TYPE IF EXISTS taskstatus")
    op.execute(
        "CREATE TYPE taskstatus AS ENUM "
        "('waiting', 'ready', 'queued', 'in_progress', 'review', 'done', 'rejected')"
    )
    op.execute("ALTER TABLE tasks ALTER COLUMN status TYPE taskstatus USING status::taskstatus")
    op.execute("ALTER TABLE task_history ALTER COLUMN from_status TYPE taskstatus USING from_status::taskstatus")
    op.execute("ALTER TABLE task_history ALTER COLUMN to_status TYPE taskstatus USING to_status::taskstatus")
