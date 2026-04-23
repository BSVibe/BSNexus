"""drop bsgateway from IntegrationProvider

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-04-23 15:00:00.000000

BSGateway is an executor kind, not an integration hook. Remove it from
the integrationprovider enum — any existing rows are deleted first.
Tenants that used BSGateway via the Integrations tab need to re-express
the setup as an ExecutorConfig row with ``executor_type="bsgateway"``.

PostgreSQL enum alteration uses the DROP/RECREATE pattern rather than
``ALTER TYPE`` so the rollback path stays clean.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "d3e4f5a6b7c8"
down_revision: str | Sequence[str] | None = "c2d3e4f5a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Remove any existing bsgateway rows first.
    op.execute(
        "DELETE FROM tenant_integration_configs WHERE provider = 'bsgateway'"
    )

    # DROP/RECREATE the enum (ALTER TYPE ... DROP VALUE isn't supported).
    op.execute(
        "ALTER TABLE tenant_integration_configs "
        "ALTER COLUMN provider TYPE VARCHAR(32) USING provider::text"
    )
    op.execute("DROP TYPE integrationprovider")
    op.execute("CREATE TYPE integrationprovider AS ENUM ('bsage', 'bsupervisor')")
    op.execute(
        "ALTER TABLE tenant_integration_configs "
        "ALTER COLUMN provider TYPE integrationprovider "
        "USING provider::integrationprovider"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE tenant_integration_configs "
        "ALTER COLUMN provider TYPE VARCHAR(32) USING provider::text"
    )
    op.execute("DROP TYPE integrationprovider")
    op.execute(
        "CREATE TYPE integrationprovider AS ENUM ('bsage', 'bsgateway', 'bsupervisor')"
    )
    op.execute(
        "ALTER TABLE tenant_integration_configs "
        "ALTER COLUMN provider TYPE integrationprovider "
        "USING provider::integrationprovider"
    )
