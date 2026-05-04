"""executor_configs.executor_type 'generic_llm' → 'llm_api'

Revision ID: l4e5f6a7b8c9
Revises: k3d4e5f6a7b8
Create Date: 2026-05-04 18:00:00.000000

Pure label rename — capability and routing semantics unchanged.
``generic_llm`` was an awkward placeholder; ``llm_api`` is what the
path actually is from the user's perspective: "call an LLM API
directly". Migration is a single ``UPDATE`` against the same column;
idempotent because the source value disappears after the first run.

Downgrade reverses the rename.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "l4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "k3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE executor_configs SET executor_type = 'llm_api' "
            "WHERE executor_type = 'generic_llm'"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE executor_configs SET executor_type = 'generic_llm' "
            "WHERE executor_type = 'llm_api'"
        )
    )
