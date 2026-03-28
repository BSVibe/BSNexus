"""add task_suggestions table

Revision ID: m5h6i7j8k9l0
Revises: l4g5h6i7j8k9
Create Date: 2026-03-28 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "m5h6i7j8k9l0"
down_revision: Union[str, None] = "l4g5h6i7j8k9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Use raw SQL to avoid SQLAlchemy metadata DDL events firing a duplicate
    # CREATE TYPE. When env.py imports models, the Enum(SuggestionStatus) on
    # TaskSuggestion registers a DDL listener on Base.metadata. During
    # op.create_table, that listener fires CREATE TYPE without checkfirst,
    # even if the migration column uses create_type=False (different instance).
    # Raw SQL bypasses this entirely.
    op.execute(sa.text(
        "DO $$ BEGIN "
        "CREATE TYPE suggestionstatus AS ENUM ('pending', 'approved', 'rejected', 'modified'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; "
        "END $$"
    ))
    op.execute(sa.text("""
        CREATE TABLE task_suggestions (
            id UUID NOT NULL PRIMARY KEY,
            project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            title VARCHAR(500) NOT NULL,
            description TEXT,
            task_type VARCHAR(50) NOT NULL,
            priority INTEGER NOT NULL,
            estimated_effort VARCHAR(50),
            reasoning TEXT,
            status suggestionstatus NOT NULL DEFAULT 'pending',
            rejection_reason TEXT,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        )
    """))
    op.execute(sa.text(
        "CREATE INDEX ix_task_suggestions_project_status ON task_suggestions (project_id, status)"
    ))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_task_suggestions_project_status"))
    op.execute(sa.text("DROP TABLE IF EXISTS task_suggestions"))
    op.execute(sa.text("DROP TYPE IF EXISTS suggestionstatus"))
