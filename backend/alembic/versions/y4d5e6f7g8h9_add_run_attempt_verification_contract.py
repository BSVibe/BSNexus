"""Add ``verification_contract`` JSON column to run_attempts

Revision ID: y4d5e6f7g8h9
Revises: x3c4d5e6f7g8
Create Date: 2026-05-17 11:00:00.000000

Verification Contract system. The work LLM declares — before doing the
work — how the work step is to be verified (a list of ``command`` /
``judge`` checks). That declared contract is persisted here so the
verifier can execute it instead of guessing the stack.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "y4d5e6f7g8h9"
down_revision: Union[str, Sequence[str], None] = "x3c4d5e6f7g8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("run_attempts", sa.Column("verification_contract", sa.JSON(), nullable=True))
    # The verifier now executes the declared contract: each ``command``
    # check is a ``declared_command`` aspect, each ``judge`` check an
    # ``llm_judge`` aspect. ALTER TYPE ADD VALUE runs in an autocommit
    # block (cannot run inside a transaction on older PG).
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE proof_aspect_type ADD VALUE IF NOT EXISTS 'declared_command'")
        op.execute("ALTER TYPE proof_aspect_type ADD VALUE IF NOT EXISTS 'llm_judge'")


def downgrade() -> None:
    op.drop_column("run_attempts", "verification_contract")
    # PG doesn't support DROP VALUE on an enum — the rolled-back code
    # simply stops generating declared_command / llm_judge rows.

