"""Add ``handoff`` JSON column to run_attempts

Revision ID: x3c4d5e6f7g8
Revises: w2b3c4d5e6f7
Create Date: 2026-05-16 19:30:00.000000

Tier 1 budget/continuation system. When a RunAttempt exhausts its
work-round budget with real progress, a model-independent handoff
record is persisted here so the next RunAttempt for the same WorkStep
can resume the work without inheriting any LLM message history.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "x3c4d5e6f7g8"
down_revision: Union[str, Sequence[str], None] = "w2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("run_attempts", sa.Column("handoff", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("run_attempts", "handoff")
