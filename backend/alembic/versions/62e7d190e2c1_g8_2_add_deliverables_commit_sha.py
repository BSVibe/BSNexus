"""G8.2 add deliverables.commit_sha

Revision ID: 62e7d190e2c1
Revises: r2s3t4u5v6w7
Create Date: 2026-05-12 17:19:44.413740

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "62e7d190e2c1"
down_revision: Union[str, Sequence[str], None] = "r2s3t4u5v6w7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """G8.2 — track which commit on the per-Request branch a verified
    Deliverable landed in. Nullable: not every Deliverable is bound to
    a repo (project may have no repo config), and earlier failed
    commits leave the column null so the next verify retries cleanly.
    """
    op.add_column(
        "deliverables",
        sa.Column("commit_sha", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("deliverables", "commit_sha")
