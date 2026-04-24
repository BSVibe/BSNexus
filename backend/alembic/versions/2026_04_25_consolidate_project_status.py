"""consolidate project status to active / archived

Revision ID: a1b2c3d4e5f6
Revises: f8a9b0c1d2e3
Create Date: 2026-04-25 00:30:00.000000

The original ProjectStatus enum had four values (design, active, paused,
completed) but no code path ever transitioned a project out of its
``design`` default. This collapses the enum to the two states that are
actually meaningful today:

- ``active``   — the normal state (now the default)
- ``archived`` — reserved for a future "hide from dashboard" toggle

Existing rows in any of the retired states are migrated to ``active``
(or ``archived`` for ``paused``, since that's the closest semantic
neighbor of "set aside"). The enum type is rebuilt in-place on PG
because it does not support ``DROP VALUE``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "f8a9b0c1d2e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # Retire unused values first so the enum swap can complete.
    op.execute("UPDATE projects SET status = 'active' WHERE status IN ('design', 'completed')")
    op.execute("UPDATE projects SET status = 'archived' WHERE status = 'paused'")

    if dialect == "postgresql":
        # PG can't drop enum values — recreate the type.
        op.execute("ALTER TYPE projectstatus RENAME TO projectstatus_old")
        op.execute("CREATE TYPE projectstatus AS ENUM ('active', 'archived')")
        op.execute(
            "ALTER TABLE projects "
            "ALTER COLUMN status DROP DEFAULT, "
            "ALTER COLUMN status TYPE projectstatus "
            "USING status::text::projectstatus, "
            "ALTER COLUMN status SET DEFAULT 'active'"
        )
        op.execute("DROP TYPE projectstatus_old")
    else:
        # SQLite (dev / tests) stores enums as strings — no type swap
        # needed, the UPDATEs above are sufficient.
        pass


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        op.execute("ALTER TYPE projectstatus RENAME TO projectstatus_old")
        op.execute("CREATE TYPE projectstatus AS ENUM ('design', 'active', 'paused', 'completed')")
        op.execute(
            "ALTER TABLE projects "
            "ALTER COLUMN status DROP DEFAULT, "
            "ALTER COLUMN status TYPE projectstatus "
            "USING status::text::projectstatus, "
            "ALTER COLUMN status SET DEFAULT 'design'"
        )
        op.execute("DROP TYPE projectstatus_old")
    # No-op on SQLite; archived rows remain as-is.
