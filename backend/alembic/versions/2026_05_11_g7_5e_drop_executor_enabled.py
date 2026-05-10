"""g7.5e drop executor_configs.enabled

Revision ID: r2s3t4u5v6w7
Revises: q1r2s3t4u5v6
Create Date: 2026-05-11 00:00:00.000000

The ``enabled`` toggle was a holdover from the multi-config era where
the founder could register many executors and pick one as ``is_selected``.
With one config per tenant the toggle is redundant — if the row exists
it is the active executor.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "r2s3t4u5v6w7"
down_revision: str | Sequence[str] | None = "q1r2s3t4u5v6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("executor_configs", "enabled")


def downgrade() -> None:
    op.add_column(
        "executor_configs",
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
