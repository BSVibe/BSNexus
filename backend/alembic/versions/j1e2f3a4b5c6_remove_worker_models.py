"""remove worker and registration_token models

Revision ID: j1e2f3a4b5c6
Revises: i0d1e2f3a4b5
Create Date: 2026-03-20 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "j1e2f3a4b5c6"
down_revision: Union[str, None] = "i0d1e2f3a4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Drop tasks.worker_id FK, index, and column
    op.drop_index("ix_tasks_worker_id", table_name="tasks")
    op.drop_constraint("tasks_worker_id_fkey", "tasks", type_="foreignkey")
    op.drop_column("tasks", "worker_id")

    # 2. Drop tasks.reviewer_id FK and column
    op.drop_constraint("tasks_reviewer_id_fkey", "tasks", type_="foreignkey")
    op.drop_column("tasks", "reviewer_id")

    # 3. Drop design_sessions.worker_id FK and column
    op.drop_constraint("fk_design_sessions_worker_id", "design_sessions", type_="foreignkey")
    op.drop_column("design_sessions", "worker_id")

    # 4. Drop registration_tokens table
    op.drop_index("ix_registration_tokens_token", table_name="registration_tokens")
    op.drop_table("registration_tokens")

    # 5. Drop workers table (all FK references already removed)
    op.drop_constraint("fk_workers_project_id", "workers", type_="foreignkey")
    op.drop_table("workers")

    # 6. Drop workerstatus enum
    sa.Enum(name="workerstatus").drop(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    # 1. Recreate workerstatus enum
    workerstatus = sa.Enum("idle", "busy", "offline", name="workerstatus")
    workerstatus.create(op.get_bind(), checkfirst=True)

    # 2. Recreate workers table
    op.create_table(
        "workers",
        sa.Column("id", sa.Uuid(), nullable=False, default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("platform", sa.String(length=50), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=True),
        sa.Column("status", workerstatus, nullable=False),
        sa.Column("current_task_id", sa.Uuid(), nullable=True),
        sa.Column("executor_type", sa.String(length=50), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("registered_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_foreign_key("fk_workers_project_id", "workers", "projects", ["project_id"], ["id"], ondelete="SET NULL")

    # 3. Recreate registration_tokens table
    op.create_table(
        "registration_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("token", sa.String(255), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_registration_tokens_token", "registration_tokens", ["token"])

    # 4. Restore design_sessions.worker_id
    op.add_column("design_sessions", sa.Column("worker_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_design_sessions_worker_id", "design_sessions", "workers", ["worker_id"], ["id"], ondelete="SET NULL"
    )

    # 5. Restore tasks.worker_id and tasks.reviewer_id
    op.add_column("tasks", sa.Column("worker_id", sa.Uuid(), nullable=True))
    op.add_column("tasks", sa.Column("reviewer_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("tasks_worker_id_fkey", "tasks", "workers", ["worker_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("tasks_reviewer_id_fkey", "tasks", "workers", ["reviewer_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_tasks_worker_id", "tasks", ["worker_id"])
