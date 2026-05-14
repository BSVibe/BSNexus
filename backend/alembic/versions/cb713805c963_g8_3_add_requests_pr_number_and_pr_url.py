"""G8.3 add requests.pr_number and pr_url

Revision ID: cb713805c963
Revises: 62e7d190e2c1
Create Date: 2026-05-12 17:47:24.491059

NOTE (G9, 2026-05-15): the original G8.3 commit (9b09ae7) shipped this
file as the empty ``alembic revision`` scaffold — the ``op.add_column``
body never landed. ``alembic upgrade head`` "succeeded" doing nothing,
so a fresh DB came up without ``requests.pr_number`` / ``pr_url`` and
the first Direction POST 500'd on the missing column. Body restored
here; the fresh-PG smoke test is extended to compare model metadata
against the live schema so an empty migration can't pass again.

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "cb713805c963"
down_revision: Union[str, Sequence[str], None] = "62e7d190e2c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """G8.3 — track the GitHub PR a shipped Request was opened against.

    Both columns nullable: not every Request has a repo binding (no
    repo config on the project), and an attempted-but-failed PR
    creation leaves them null so the next manual retry runs cleanly.
    ``pr_number`` is GitHub's per-repo integer; ``pr_url`` is the full
    ``html_url`` for direct linking from the founder Brief.
    """
    op.add_column("requests", sa.Column("pr_number", sa.Integer(), nullable=True))
    op.add_column("requests", sa.Column("pr_url", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("requests", "pr_url")
    op.drop_column("requests", "pr_number")
