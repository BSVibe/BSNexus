"""add directive column to execution_runs

Revision ID: e7f8a9b0c1d2
Revises: d3e4f5a6b7c8
Create Date: 2026-04-24 01:00:00.000000

Per-run user direction text. Carries the phase-specific prompt for
planner-seeded child runs; for top-level runs it mirrors
``request.intent_summary`` (and can stay NULL — orchestrator falls
back to the request's summary when directive is unset).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7f8a9b0c1d2"
down_revision: str | Sequence[str] | None = "d3e4f5a6b7c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "execution_runs",
        sa.Column("directive", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("execution_runs", "directive")
