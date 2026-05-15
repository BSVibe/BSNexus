"""Add ``code_build`` to proof_aspect_type enum

Revision ID: w2b3c4d5e6f7
Revises: v1a2b3c4d5e6f
Create Date: 2026-05-15 18:00:00.000000

New aspect for Dockerfile build smoke. Adds the enum value via
``ALTER TYPE ADD VALUE`` (PG 9.1+).
"""

from typing import Sequence, Union

from alembic import op

revision: str = "w2b3c4d5e6f7"
down_revision: Union[str, Sequence[str], None] = "v1a2b3c4d5e6f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ADD VALUE must run outside an explicit transaction
    # on older PG. autocommit_block makes it work everywhere.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE proof_aspect_type ADD VALUE IF NOT EXISTS 'code_build'")


def downgrade() -> None:
    # PG doesn't support DROP VALUE on an enum. Downgrade is a no-op;
    # the rolled-back code simply won't generate ``code_build`` rows.
    # If a hard rollback is needed, re-create the enum without the
    # value (DROP TYPE + CREATE TYPE) — out of scope for this stop-gap.
    pass
