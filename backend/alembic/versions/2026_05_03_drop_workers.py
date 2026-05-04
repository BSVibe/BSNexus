"""drop workers table + execution_runs.worker_id column

Revision ID: i1b2c3d4e5f6
Revises: h0a1b2c3d4e5
Create Date: 2026-05-03 00:00:00.000000

Direction reset 2026-05-03 — BSNexus no longer hosts workers; BSGateway
owns the worker pool. The earlier ``j1e2f3a4b5c6_remove_worker_models``
migration dropped ``workers`` once, but ``founder_metaphor`` later
recreated an FK to it on ``execution_runs.worker_id``, leaving the
chain in an ambiguous state across SQLite (FK constraints ignored at
table-create time) and PostgreSQL (FK enforced — alembic upgrade head
already half-handled this).

This migration is **idempotent**: it inspects the live schema and only
drops what's actually there. SQLite is exercised by the test suite;
PostgreSQL is exercised by the fresh-PG smoke
(``test_alembic_fresh_migration``).

Cleanup:
- Drop FK constraint ``execution_runs_worker_id_fkey`` (PG only — SQLite
  doesn't track FK constraints by name).
- Drop the ``worker_id`` column from ``execution_runs``.
- Drop the ``workers`` table if it still exists.
- Drop the ``workerstatus`` enum (PG only) so the next bring-up doesn't
  collide.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "i1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "h0a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_exists(inspector: sa.engine.reflection.Inspector, name: str) -> bool:
    return name in inspector.get_table_names()


def _column_exists(inspector: sa.engine.reflection.Inspector, table: str, column: str) -> bool:
    if not _table_exists(inspector, table):
        return False
    return any(c["name"] == column for c in inspector.get_columns(table))


def _fk_exists(
    inspector: sa.engine.reflection.Inspector, table: str, column: str, ref_table: str
) -> bool:
    if not _table_exists(inspector, table):
        return False
    for fk in inspector.get_foreign_keys(table):
        if column in (fk.get("constrained_columns") or []) and fk.get("referred_table") == ref_table:
            return True
    return False


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    inspector = sa.inspect(bind)

    # 1. Drop FK + column on execution_runs (if present).
    if _column_exists(inspector, "execution_runs", "worker_id"):
        if dialect == "postgresql" and _fk_exists(
            inspector, "execution_runs", "worker_id", "workers"
        ):
            # FK names vary across migrations — ask the inspector for the
            # actual constraint name rather than guessing.
            fks = inspector.get_foreign_keys("execution_runs")
            for fk in fks:
                if "worker_id" in (fk.get("constrained_columns") or []):
                    name = fk.get("name")
                    if name:
                        op.drop_constraint(name, "execution_runs", type_="foreignkey")
                    break

        # SQLite needs batch_alter_table to drop a column; PG accepts
        # a plain ALTER.
        if dialect == "sqlite":
            with op.batch_alter_table("execution_runs") as batch_op:
                batch_op.drop_column("worker_id")
        else:
            op.drop_column("execution_runs", "worker_id")

    # 2. Drop the workers table (if it survived earlier migrations).
    if _table_exists(inspector, "workers"):
        op.drop_table("workers")

    # 3. Drop the workerstatus enum (PG only).
    if dialect == "postgresql":
        op.execute("DROP TYPE IF EXISTS workerstatus")


def downgrade() -> None:
    """Best-effort downgrade — recreates the ``execution_runs.worker_id``
    column without an FK so the schema satisfies any code path still
    looking for the column. Recreating the full ``workers`` table is
    out of scope: the BSNexus-side worker stack is gone in this branch
    and won't come back."""
    op.add_column(
        "execution_runs",
        sa.Column("worker_id", sa.Uuid(), nullable=True),
    )
