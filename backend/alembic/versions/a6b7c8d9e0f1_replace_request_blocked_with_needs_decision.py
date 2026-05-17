"""Replace request_status 'blocked' with 'needs_decision'

Revision ID: a6b7c8d9e0f1
Revises: z5e6f7g8h9i0
Create Date: 2026-05-18 09:00:00.000000

The ``blocked`` request status was a silent dead-end — a Request that
could not finish sat there forever with no founder recourse. It is
retired in favour of ``needs_decision``: a stuck Request now raises a
forward-only founder Decision and waits in ``needs_decision`` until the
founder resolves it.

The ``request_status`` PG enum is rebuilt via the VARCHAR-swap idiom
(``ALTER TYPE ADD VALUE`` cannot run inside a migration transaction and
cannot drop a value at all). Existing ``blocked`` rows are migrated to
``needs_decision``. The ``work_step_status`` enum already carries
``needs_decision`` — no change there.

Also adds ``decisions.guidance`` — the founder's free-text direction
supplied when a blocking Decision is resolved with ``reframe``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a6b7c8d9e0f1"
down_revision: str | Sequence[str] | None = "z5e6f7g8h9i0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute("ALTER TABLE requests ALTER COLUMN status DROP DEFAULT")
        op.execute("ALTER TABLE requests ALTER COLUMN status TYPE VARCHAR(32)")
        op.execute("UPDATE requests SET status = 'needs_decision' WHERE status = 'blocked'")
        op.execute("DROP TYPE request_status")
        op.execute(
            "CREATE TYPE request_status AS ENUM "
            "('open', 'running', 'needs_decision', 'review_ready', 'shipped', 'abandoned')"
        )
        op.execute("ALTER TABLE requests ALTER COLUMN status TYPE request_status USING status::request_status")
        op.execute("ALTER TABLE requests ALTER COLUMN status SET DEFAULT 'open'::request_status")
    else:
        # SQLite (dev / tests) stores enums as strings — just remap.
        op.execute("UPDATE requests SET status = 'needs_decision' WHERE status = 'blocked'")

    op.add_column("decisions", sa.Column("guidance", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("decisions", "guidance")

    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute("ALTER TABLE requests ALTER COLUMN status DROP DEFAULT")
        op.execute("ALTER TABLE requests ALTER COLUMN status TYPE VARCHAR(32)")
        op.execute("UPDATE requests SET status = 'blocked' WHERE status = 'needs_decision'")
        op.execute("DROP TYPE request_status")
        op.execute(
            "CREATE TYPE request_status AS ENUM ('open', 'running', 'blocked', 'review_ready', 'shipped', 'abandoned')"
        )
        op.execute("ALTER TABLE requests ALTER COLUMN status TYPE request_status USING status::request_status")
        op.execute("ALTER TABLE requests ALTER COLUMN status SET DEFAULT 'open'::request_status")
    else:
        op.execute("UPDATE requests SET status = 'blocked' WHERE status = 'needs_decision'")
