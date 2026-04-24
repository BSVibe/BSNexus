"""add originator_auth column to requests

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-04-24 12:45:00.000000

Raw Bearer token from the founder's originating HTTP request. Used
when BSNexus calls sibling services (BSage knowledge writes) on the
founder's behalf — forwarding the same JWT avoids a separate service
API key and preserves per-user attribution downstream.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f8a9b0c1d2e3"
down_revision: str | Sequence[str] | None = "e7f8a9b0c1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "requests",
        sa.Column("originator_auth", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("requests", "originator_auth")
