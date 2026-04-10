"""simplify TaskStatus to 4 states, drop DesignSession + architect

Revision ID: 9a8b7c6d5e4f
Revises: 7f0919a64c39
Create Date: 2026-04-10 12:00:00.000000

Migrates the task lifecycle from 6 states (waiting, ready, in_progress,
review, done, redesign) to 4 states (pending, running, blocked, done):

  waiting, ready             -> pending
  in_progress, review        -> running
  redesign                   -> blocked
  done                       -> done

Also drops the legacy DesignSession / DesignMessage tables and the
TaskSource.architect enum value (renamed to TaskSource.llm).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9a8b7c6d5e4f"
down_revision: str | Sequence[str] | None = "7f0919a64c39"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ── upgrade ──────────────────────────────────────────────────────────


def upgrade() -> None:
    # 1. Drop DesignSession + DesignMessage tables (CASCADE wipes FKs).
    op.execute("DROP TABLE IF EXISTS design_messages CASCADE")
    op.execute("DROP TABLE IF EXISTS design_sessions CASCADE")

    # 2. Drop architect-only enums.
    op.execute("DROP TYPE IF EXISTS designsessionstatus")
    op.execute("DROP TYPE IF EXISTS messagerole")
    op.execute("DROP TYPE IF EXISTS messagetype")

    # 3. Re-create tasksource enum with 'llm' instead of 'architect'.
    op.execute("ALTER TABLE tasks ALTER COLUMN source TYPE VARCHAR(20)")
    op.execute("UPDATE tasks SET source = 'llm' WHERE source = 'architect'")
    op.execute("DROP TYPE IF EXISTS tasksource")
    op.execute("CREATE TYPE tasksource AS ENUM ('llm', 'auto_bug', 'manual')")
    op.execute("ALTER TABLE tasks ALTER COLUMN source TYPE tasksource USING source::tasksource")
    op.execute("ALTER TABLE tasks ALTER COLUMN source SET DEFAULT 'llm'")

    # 4. Collapse TaskStatus to the 4-state model.
    #    Cast to VARCHAR, rewrite values, recreate enum, cast back.
    op.execute("ALTER TABLE tasks ALTER COLUMN status TYPE VARCHAR(20)")
    op.execute("ALTER TABLE task_history ALTER COLUMN from_status TYPE VARCHAR(50)")
    op.execute("ALTER TABLE task_history ALTER COLUMN to_status TYPE VARCHAR(50)")

    # Rewrite live data on tasks.
    op.execute("UPDATE tasks SET status = 'pending' WHERE status IN ('waiting', 'ready', 'queued')")
    op.execute("UPDATE tasks SET status = 'running' WHERE status IN ('in_progress', 'review')")
    op.execute("UPDATE tasks SET status = 'blocked' WHERE status IN ('redesign', 'rejected')")

    # Rewrite history audit log too so the column type can be re-applied cleanly.
    op.execute("UPDATE task_history SET from_status = 'pending' WHERE from_status IN ('waiting', 'ready', 'queued')")
    op.execute("UPDATE task_history SET from_status = 'running' WHERE from_status IN ('in_progress', 'review')")
    op.execute("UPDATE task_history SET from_status = 'blocked' WHERE from_status IN ('redesign', 'rejected')")
    op.execute("UPDATE task_history SET to_status = 'pending' WHERE to_status IN ('waiting', 'ready', 'queued')")
    op.execute("UPDATE task_history SET to_status = 'running' WHERE to_status IN ('in_progress', 'review')")
    op.execute("UPDATE task_history SET to_status = 'blocked' WHERE to_status IN ('redesign', 'rejected')")

    op.execute("DROP TYPE IF EXISTS taskstatus")
    op.execute("CREATE TYPE taskstatus AS ENUM ('pending', 'running', 'blocked', 'done')")
    op.execute("ALTER TABLE tasks ALTER COLUMN status TYPE taskstatus USING status::taskstatus")
    op.execute("ALTER TABLE tasks ALTER COLUMN status SET DEFAULT 'pending'")
    op.execute(
        "ALTER TABLE task_history ALTER COLUMN from_status TYPE taskstatus USING from_status::taskstatus"
    )
    op.execute("ALTER TABLE task_history ALTER COLUMN to_status TYPE taskstatus USING to_status::taskstatus")


# ── downgrade ────────────────────────────────────────────────────────


def downgrade() -> None:
    """No downgrade. The branch is dev-stage and intentionally lossy.

    The 4-state collapse cannot be safely reversed: distinguishing
    waiting/ready or in_progress/review would require information that
    no longer exists in the database.
    """
    raise NotImplementedError(
        "downgrade is intentionally not supported for the 4-state TaskStatus migration"
    )
