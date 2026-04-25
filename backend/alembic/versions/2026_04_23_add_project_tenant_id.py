"""add tenant_id to projects

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-04-23 12:00:00.000000

Projects become tenant-scoped (was: scoped via TenantMember only). FK
cascades on tenant deletion so test/dev teardown is clean.

Prod strategy: drop-and-recreate for beta (no in-place backfill). The
previous migration already wiped retired tables.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | Sequence[str] | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.create_index("ix_projects_tenant", "projects", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_projects_tenant", table_name="projects")
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("tenant_id")
