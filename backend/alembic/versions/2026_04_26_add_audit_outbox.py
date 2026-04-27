"""add audit_outbox table

Revision ID: h0a1b2c3d4e5
Revises: g9a8b7c6d5e4
Create Date: 2026-04-26 00:00:00.000000

Phase Audit Batch 2 — adopt the ``bsvibe-audit`` outbox-pattern table
inside the BSNexus database. Every domain mutation (project / request /
run transition / deliverable / decision) now writes one ``audit_outbox``
row inside the same transaction; a background ``OutboxRelay`` worker
ships them to BSVibe-Auth's ``POST /api/audit/events`` endpoint.

The table shape mirrors the ``bsvibe_audit.outbox.schema`` SQLAlchemy
model (``register_audit_outbox_with(Base.metadata)`` already attaches
it to autogenerate). Hand-written here so the migration is explicit,
reviewable, and stable across package versions — the package may add
columns later but the wire shape we control is this one.

Index ``ix_audit_outbox_undelivered (delivered_at, next_attempt_at)``
drives the relay's ``select_undelivered`` predicate; without it the
poll degrades to a full scan once the outbox grows.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "h0a1b2c3d4e5"
down_revision: str | Sequence[str] | None = "g9a8b7c6d5e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # ``BigInteger`` autoincrement on PG, plain ``Integer`` on SQLite —
    # SQLite reuses ``rowid`` for autoincrement and refuses ``BIGINT``
    # primary keys with ``AUTOINCREMENT``. Mirror the package model's
    # ``with_variant`` switch.
    id_type: sa.types.TypeEngine
    if dialect == "sqlite":
        id_type = sa.Integer()
    else:
        id_type = sa.BigInteger()

    op.create_table(
        "audit_outbox",
        sa.Column("id", id_type, primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_letter", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("event_id", name="uq_audit_outbox_event_id"),
    )
    op.create_index(
        "ix_audit_outbox_undelivered",
        "audit_outbox",
        ["delivered_at", "next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_outbox_undelivered", table_name="audit_outbox")
    op.drop_table("audit_outbox")
