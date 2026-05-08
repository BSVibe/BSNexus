"""Add proof model columns to deliverables (decision-locks A1).

Revision ID: m5f6a7b8c9d0
Revises: l4e5f6a7b8c9
Create Date: 2026-05-08 12:00:00.000000

Adds the proof fields the Verifier Worker (decision-locks A1, 2026-05-08)
stamps onto Deliverables:

- proof_state (PG enum)
- verifier_type (varchar)
- verifier_inputs (JSON / JSONB on PG)
- verification_exit_code (integer)
- proof_summary (text)
- proof_refs (JSON / JSONB on PG)
- risk_summary (text)
- verified_at (timestamptz)
- ix_deliverables_project_proof_state composite index for the Brief and
  Decision Inbox-style filters that scope by project + proof state.

Backfill: every existing row gets ``proof_state = 'verification_missing'``
via the column-level ``server_default``. After the column is in place the
default stays — every new row defaults to ``verification_missing`` unless
the orchestrator stamps a different state explicitly.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "m5f6a7b8c9d0"
down_revision: str | Sequence[str] | None = "l4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_PROOF_STATE_VALUES = (
    "verification_missing",
    "verifying",
    "verified",
    "verification_failed",
    "human_review_required",
    "not_applicable",
)


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    if is_pg:
        proof_state_pg = postgresql.ENUM(*_PROOF_STATE_VALUES, name="proofstate", create_type=False)
        proof_state_pg.create(bind, checkfirst=True)
        proof_state_col = sa.Enum(
            *_PROOF_STATE_VALUES, name="proofstate", native_enum=True, create_constraint=False
        )
        json_type: sa.types.TypeEngine = postgresql.JSONB(astext_type=sa.Text())
    else:
        # SQLite test path — store enum as VARCHAR via SQLAlchemy's
        # non-native fallback; JSON column type is portable.
        proof_state_col = sa.Enum(*_PROOF_STATE_VALUES, name="proofstate", native_enum=False)
        json_type = sa.JSON()

    op.add_column(
        "deliverables",
        sa.Column(
            "proof_state",
            proof_state_col,
            nullable=False,
            server_default="verification_missing",
        ),
    )
    op.add_column("deliverables", sa.Column("verifier_type", sa.String(length=64), nullable=True))
    op.add_column("deliverables", sa.Column("verifier_inputs", json_type, nullable=True))
    op.add_column("deliverables", sa.Column("verification_exit_code", sa.Integer(), nullable=True))
    op.add_column("deliverables", sa.Column("proof_summary", sa.Text(), nullable=True))
    op.add_column("deliverables", sa.Column("proof_refs", json_type, nullable=True))
    op.add_column("deliverables", sa.Column("risk_summary", sa.Text(), nullable=True))
    op.add_column(
        "deliverables",
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index(
        "ix_deliverables_project_proof_state",
        "deliverables",
        ["project_id", "proof_state"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    op.drop_index("ix_deliverables_project_proof_state", table_name="deliverables")

    op.drop_column("deliverables", "verified_at")
    op.drop_column("deliverables", "risk_summary")
    op.drop_column("deliverables", "proof_refs")
    op.drop_column("deliverables", "proof_summary")
    op.drop_column("deliverables", "verification_exit_code")
    op.drop_column("deliverables", "verifier_inputs")
    op.drop_column("deliverables", "verifier_type")
    op.drop_column("deliverables", "proof_state")

    if is_pg:
        proof_state_pg = postgresql.ENUM(*_PROOF_STATE_VALUES, name="proofstate", create_type=False)
        proof_state_pg.drop(bind, checkfirst=True)
