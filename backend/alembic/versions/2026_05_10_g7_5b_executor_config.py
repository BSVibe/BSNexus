"""g7.5b executor_configs (per-tenant LLM dispatch)

Revision ID: q1r2s3t4u5v6
Revises: p0q1r2s3t4u5
Create Date: 2026-05-10 00:00:00.000000

Re-introduces the executor_configs table after the G0 greenfield reset.
One config row per tenant; ``kind`` switches between bsgateway worker
pool and direct litellm. ``api_key_encrypted`` stored via
EncryptionManager.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "q1r2s3t4u5v6"
down_revision: str | Sequence[str] | None = "p0q1r2s3t4u5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    from sqlalchemy.dialects.postgresql import ENUM as PGEnum

    if is_postgres:
        op.execute(sa.text("CREATE TYPE executor_kind AS ENUM ('bsgateway', 'llm_api')"))
        executor_kind = PGEnum(
            "bsgateway",
            "llm_api",
            name="executor_kind",
            create_type=False,
        )
    else:
        executor_kind = sa.Enum("bsgateway", "llm_api", name="executor_kind")

    op.create_table(
        "executor_configs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", executor_kind, nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("base_url", sa.String(500), nullable=True),
        sa.Column("model", sa.String(255), nullable=True),
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
        sa.UniqueConstraint("tenant_id", name="uq_executor_config_tenant"),
    )
    op.create_index("ix_executor_configs_tenant", "executor_configs", ["tenant_id"])


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    op.drop_index("ix_executor_configs_tenant", table_name="executor_configs")
    op.drop_table("executor_configs")

    if is_postgres:
        op.execute(sa.text("DROP TYPE IF EXISTS executor_kind"))
