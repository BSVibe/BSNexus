"""Drop the UNIQUE constraint on tenants.slug

Revision ID: z5e6f7g8h9i0
Revises: y4d5e6f7g8h9
Create Date: 2026-05-17 13:00:00.000000

BSNexus's ``tenants`` table is a derived projection of BSVibe-owned
tenant identity. ``slug`` mirrors the user id and is never queried — it
is informational only. The UNIQUE constraint on it is a latent bug:
when BSVibe reassigns a user's tenant id, ``ensure_personal_tenant``
must project a fresh row for the new id, but the old row still holds
the slug — the insert hit ``tenants_slug_key`` and was swallowed, so
the new tenant row never existed and every write under the new tenant
id FK-violated (a founder could not submit any Direction). Dropping the
constraint lets the new projection row land; the stale row is harmless.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "z5e6f7g8h9i0"
down_revision: Union[str, Sequence[str], None] = "y4d5e6f7g8h9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE tenants DROP CONSTRAINT IF EXISTS tenants_slug_key")


def downgrade() -> None:
    # Re-adding the UNIQUE would fail whenever two projection rows share
    # a slug (the exact state this migration exists to allow), so the
    # downgrade is intentionally a no-op.
    pass
