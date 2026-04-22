"""add worker_install_token_hash to tenants

Revision ID: f1a2b3c4d5e6
Revises: e0f1a2b3c4d5
Create Date: 2026-04-10 14:00:00.000000

The worker install token used to be a global Setting row. Multi-tenant
deployments need it scoped per tenant so a worker installed with token
T is automatically assigned to the tenant that minted T.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "e0f1a2b3c4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("worker_install_token_hash", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenants", "worker_install_token_hash")
