"""initial models

Revision ID: ed4a4a1b1581
Revises:
Create Date: 2026-02-06 07:41:58.199431

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ed4a4a1b1581"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Enum types
    projectstatus = sa.Enum("design", "active", "paused", "completed", name="projectstatus")
    phasestatus = sa.Enum("pending", "active", "completed", name="phasestatus")
    taskstatus = sa.Enum("waiting", "ready", "queued", "in_progress", "review", "done", "rejected", name="taskstatus")
    taskpriority = sa.Enum("low", "medium", "high", "critical", name="taskpriority")
    workerstatus = sa.Enum("idle", "busy", "offline", name="workerstatus")
    designsessionstatus = sa.Enum("active", "finalized", "cancelled", name="designsessionstatus")
    messagerole = sa.Enum("user", "assistant", name="messagerole")

    # projects
    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), nullable=False, default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("design_doc_path", sa.String(length=500), nullable=True),
        sa.Column("repo_path", sa.String(length=500), nullable=False),
        sa.Column("status", projectstatus, nullable=False),
        sa.Column("llm_config", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # workers
    op.create_table(
        "workers",
        sa.Column("id", sa.Uuid(), nullable=False, default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("platform", sa.String(length=50), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=True),
        sa.Column("status", workerstatus, nullable=False),
        sa.Column("current_task_id", sa.Uuid(), nullable=True),
        sa.Column("executor_type", sa.String(length=50), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # phases
    op.create_table(
        "phases",
        sa.Column("id", sa.Uuid(), nullable=False, default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("branch_name", sa.String(length=255), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("status", phasestatus, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
    )

    # design_sessions
    op.create_table(
        "design_sessions",
        sa.Column("id", sa.Uuid(), nullable=False, default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("status", designsessionstatus, nullable=False),
        sa.Column("llm_config", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
    )

    # design_messages
    op.create_table(
        "design_messages",
        sa.Column("id", sa.Uuid(), nullable=False, default=sa.text("gen_random_uuid()")),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("role", messagerole, nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["session_id"], ["design_sessions.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_design_messages_session_created", "design_messages", ["session_id", "created_at"])

    # tasks
    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(), nullable=False, default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("phase_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", taskstatus, nullable=False),
        sa.Column("priority", taskpriority, nullable=False),
        sa.Column("worker_prompt", sa.JSON(), nullable=True),
        sa.Column("qa_prompt", sa.JSON(), nullable=True),
        sa.Column("branch_name", sa.String(length=255), nullable=True),
        sa.Column("commit_hash", sa.String(length=64), nullable=True),
        sa.Column("worker_id", sa.Uuid(), nullable=True),
        sa.Column("reviewer_id", sa.Uuid(), nullable=True),
        sa.Column("qa_result", sa.JSON(), nullable=True),
        sa.Column("output_path", sa.String(length=500), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["phase_id"], ["phases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["worker_id"], ["workers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reviewer_id"], ["workers.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_tasks_project_status", "tasks", ["project_id", "status"])
    op.create_index("ix_tasks_phase_status", "tasks", ["phase_id", "status"])
    op.create_index("ix_tasks_worker_id", "tasks", ["worker_id"])

    # task_dependencies
    op.create_table(
        "task_dependencies",
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("dependency_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("task_id", "dependency_id"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dependency_id"], ["tasks.id"], ondelete="CASCADE"),
    )

    # task_history
    op.create_table(
        "task_history",
        sa.Column("id", sa.Uuid(), nullable=False, default=sa.text("gen_random_uuid()")),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("from_status", sa.String(length=50), nullable=False),
        sa.Column("to_status", sa.String(length=50), nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_task_history_task_timestamp", "task_history", ["task_id", "timestamp"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("task_history")
    op.drop_table("task_dependencies")
    op.drop_table("tasks")
    op.drop_table("design_messages")
    op.drop_table("design_sessions")
    op.drop_table("phases")
    op.drop_table("workers")
    op.drop_table("projects")

    # Drop enum types
    sa.Enum(name="messagerole").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="designsessionstatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="workerstatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="taskpriority").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="taskstatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="phasestatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="projectstatus").drop(op.get_bind(), checkfirst=True)
