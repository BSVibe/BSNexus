"""Add run_summary JSONB column to execution_runs (PR7 — failure-mode instrumentation).

Revision ID: n6g7h8i9j0k1
Revises: m5f6a7b8c9d0
Create Date: 2026-05-08 16:00:00.000000

Adds the per-run aggregate that PR7's classifier + computation hook
populate at terminal-state transition. Pre-existing rows stay NULL —
the next finalize pass through them will stamp on next run only;
historical runs are not back-computed.

JSON column add only — no enum, so the standard DROP/RECREATE pattern
from ``alembic-postgres-enum-migration`` does NOT apply.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "n6g7h8i9j0k1"
down_revision: str | Sequence[str] | None = "m5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "execution_runs",
        sa.Column(
            "run_summary",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("execution_runs", "run_summary")
