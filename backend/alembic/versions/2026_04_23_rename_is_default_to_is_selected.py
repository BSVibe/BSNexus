"""rename executor_configs.is_default → is_selected

Revision ID: c2d3e4f5a6b7
Revises: f6a7b8c9d0e1
Create Date: 2026-04-23 14:00:00.000000

Tenants can register many ExecutorConfigs but exactly one is consulted
by the orchestrator when dispatching runs. Renaming the flag from
``is_default`` to ``is_selected`` makes that exclusive-selection
semantic explicit and avoids confusion with ``Worker.is_active`` /
generic "default" meaning.

Column, partial-unique index, and all Python references are renamed
together.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c2d3e4f5a6b7"
down_revision: str | Sequence[str] | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop the partial unique index (it references the old column name)
    op.drop_index(
        "uq_executor_configs_one_default_per_tenant",
        table_name="executor_configs",
    )
    # Rename the column
    op.alter_column(
        "executor_configs",
        "is_default",
        new_column_name="is_selected",
    )
    # Recreate the partial unique index against the new column name
    op.create_index(
        "uq_executor_configs_one_selected_per_tenant",
        "executor_configs",
        ["tenant_id"],
        unique=True,
        postgresql_where="is_selected = true",
    )


def downgrade() -> None:
    op.drop_index(
        "uq_executor_configs_one_selected_per_tenant",
        table_name="executor_configs",
    )
    op.alter_column(
        "executor_configs",
        "is_selected",
        new_column_name="is_default",
    )
    op.create_index(
        "uq_executor_configs_one_default_per_tenant",
        "executor_configs",
        ["tenant_id"],
        unique=True,
        postgresql_where="is_default = true",
    )
