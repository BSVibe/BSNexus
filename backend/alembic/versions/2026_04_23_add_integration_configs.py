"""add tenant_integration_configs

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-04-23 11:00:00.000000

Per-tenant settings for BSage / BSGateway / BSupervisor integrations.
Encrypted api_key at rest.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | Sequence[str] | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    integration_provider = sa.Enum(
        "bsage", "bsgateway", "bsupervisor", name="integrationprovider"
    )
    if is_postgres:
        integration_provider.create(bind, checkfirst=True)

    op.create_table(
        "tenant_integration_configs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", integration_provider, nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("base_url", sa.String(500), nullable=True),
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("extra_config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "tenant_id", "provider", name="uq_tenant_integration_provider"
        ),
    )
    op.create_index(
        "ix_tenant_integration_configs_tenant", "tenant_integration_configs", ["tenant_id"]
    )


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    op.drop_index(
        "ix_tenant_integration_configs_tenant",
        table_name="tenant_integration_configs",
    )
    op.drop_table("tenant_integration_configs")

    if is_postgres:
        op.execute(sa.text("DROP TYPE IF EXISTS integrationprovider"))
