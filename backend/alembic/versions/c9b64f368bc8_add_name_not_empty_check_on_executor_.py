"""add name not empty check on executor_configs

Revision ID: c9b64f368bc8
Revises: 0fbf942ea6c5
Create Date: 2026-04-07 23:55:43.771062

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c9b64f368bc8'
down_revision: Union[str, Sequence[str], None] = '0fbf942ea6c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_check_constraint(
        "ck_executor_configs_name_not_empty",
        "executor_configs",
        "length(name) > 0",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("ck_executor_configs_name_not_empty", "executor_configs", type_="check")
