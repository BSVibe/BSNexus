"""add executor_type and executor_metadata columns to tasks

Revision ID: l4g5h6i7j8k9
Revises: k2f3a4b5c6d7
Create Date: 2026-03-28 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "l4g5h6i7j8k9"
down_revision: Union[str, None] = "k2f3a4b5c6d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("executor_type", sa.String(50), nullable=False, server_default="coding"))
    op.add_column("tasks", sa.Column("executor_metadata", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    op.drop_column("tasks", "executor_metadata")
    op.drop_column("tasks", "executor_type")
