"""add partial unique index on tasks(phase_id, title) where status != done

Prevents duplicate task titles within the same phase at the DB level,
closing the TOCTOU race condition in CreateTaskTool's application-level
dedup check.

Revision ID: n6i7j8k9l0m1
Revises: m5h6i7j8k9l0
Create Date: 2026-04-18 20:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "n6i7j8k9l0m1"
down_revision: Union[str, None] = "ed791735ea6c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX ix_tasks_phase_title_active "
            "ON tasks (phase_id, lower(trim(title))) "
            "WHERE status != 'done'"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_tasks_phase_title_active"))
