"""add living project fields: task_type, source, parent_task_id, project_bound status

Revision ID: i0d1e2f3a4b5
Revises: 7e7c56acd1a0
Create Date: 2026-03-15 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "i0d1e2f3a4b5"
down_revision: Union[str, None] = "7e7c56acd1a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add task_type enum and column
    task_type_enum = sa.Enum("feature", "bug", "improvement", "test", "chore", "refactor", name="tasktype")
    task_type_enum.create(op.get_bind(), checkfirst=True)
    op.add_column("tasks", sa.Column("task_type", task_type_enum, nullable=False, server_default="feature"))

    # Add task_source enum and column
    task_source_enum = sa.Enum("architect", "auto_bug", "manual", name="tasksource")
    task_source_enum.create(op.get_bind(), checkfirst=True)
    op.add_column("tasks", sa.Column("source", task_source_enum, nullable=False, server_default="architect"))

    # Add parent_task_id column
    op.add_column("tasks", sa.Column("parent_task_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_tasks_parent_task_id",
        "tasks",
        "tasks",
        ["parent_task_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_tasks_parent_task_id", "tasks", ["parent_task_id"])

    # Replace 'finalized' with 'project_bound' in designsessionstatus enum
    # Use DROP/RECREATE to avoid transactional ADD VALUE issues
    op.execute("ALTER TABLE design_sessions ALTER COLUMN status TYPE VARCHAR(20)")
    op.execute("UPDATE design_sessions SET status = 'project_bound' WHERE status = 'finalized'")
    op.execute("DROP TYPE designsessionstatus")
    op.execute("CREATE TYPE designsessionstatus AS ENUM ('active', 'project_bound', 'cancelled')")
    op.execute(
        "ALTER TABLE design_sessions ALTER COLUMN status TYPE designsessionstatus USING status::designsessionstatus"
    )


def downgrade() -> None:
    # Revert designsessionstatus: project_bound back to finalized
    op.execute("ALTER TABLE design_sessions ALTER COLUMN status TYPE VARCHAR(20)")
    op.execute("UPDATE design_sessions SET status = 'finalized' WHERE status = 'project_bound'")
    op.execute("DROP TYPE designsessionstatus")
    op.execute("CREATE TYPE designsessionstatus AS ENUM ('active', 'finalized', 'cancelled')")
    op.execute(
        "ALTER TABLE design_sessions ALTER COLUMN status TYPE designsessionstatus USING status::designsessionstatus"
    )

    # Remove parent_task_id
    op.drop_index("ix_tasks_parent_task_id", table_name="tasks")
    op.drop_constraint("fk_tasks_parent_task_id", "tasks", type_="foreignkey")
    op.drop_column("tasks", "parent_task_id")

    # Remove source column and enum
    op.drop_column("tasks", "source")
    sa.Enum(name="tasksource").drop(op.get_bind(), checkfirst=True)

    # Remove task_type column and enum
    op.drop_column("tasks", "task_type")
    sa.Enum(name="tasktype").drop(op.get_bind(), checkfirst=True)
