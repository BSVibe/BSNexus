"""G8.3 add requests.pr_number and pr_url

Revision ID: cb713805c963
Revises: 62e7d190e2c1
Create Date: 2026-05-12 17:47:24.491059

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "cb713805c963"
down_revision: Union[str, Sequence[str], None] = "62e7d190e2c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
