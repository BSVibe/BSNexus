"""founder metaphor redesign

Revision ID: d4e5f6a7b8c9
Revises: o7j8k9l0m1n2
Create Date: 2026-04-23 10:00:00.000000

Replaces agent/task/mention schema with request/run/deliverable/decision.

Drops 7 tables (agents, agent_memories, agent_relationships,
agent_capabilities, goals, phases, plan_proposals). Renames tasks →
execution_runs (slimmed). Adds 5 new tables (requests,
composition_snapshots, deliverables, deliverable_versions, decisions).

Prod data strategy: drop-and-recreate for beta. Existing data is lost.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "o7j8k9l0m1n2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    # ─────────────────────────────────────────────────────────
    # 1. Drop dependent tables first (those with FKs to agents/tasks/phases)
    # ─────────────────────────────────────────────────────────

    # conversation_messages FKs point to agents + tasks
    op.drop_index("ix_conversation_messages_task", table_name="conversation_messages")
    with op.batch_alter_table("conversation_messages") as batch:
        batch.drop_column("task_id")
        batch.drop_column("agent_id")
        batch.drop_column("agent_name")

    # cost_records FKs point to agents + tasks
    op.drop_index("ix_cost_records_agent_month", table_name="cost_records")
    op.drop_index("ix_cost_records_agent", table_name="cost_records")
    with op.batch_alter_table("cost_records") as batch:
        batch.drop_column("task_id")
        batch.drop_column("agent_id")

    # task_activities references tasks + agents
    op.drop_index("ix_task_activities_task_created", table_name="task_activities")
    op.drop_index("ix_task_activities_project_created", table_name="task_activities")
    op.drop_table("task_activities")

    # task_history references tasks
    op.drop_index("ix_task_history_task_timestamp", table_name="task_history")
    op.drop_table("task_history")

    # task_dependencies
    op.drop_table("task_dependencies")

    # plan_proposals references agents/phases
    op.drop_table("plan_proposals")

    # agent_memories references agents
    op.drop_table("agent_memories")

    # tasks references phases/agents/goals
    op.drop_index("ix_tasks_project_status", table_name="tasks")
    op.drop_index("ix_tasks_phase_status", table_name="tasks")
    op.drop_table("tasks")

    # phases references projects
    op.drop_table("phases")

    # goals references agents/projects/tenants
    op.drop_table("goals")

    # agents is the core of the removed org-chart
    op.drop_table("agents")

    if is_postgres:
        for enum_name in (
            "taskstatus",
            "taskpriority",
            "tasktype",
            "tasksource",
            "activitylevel",
            "phasestatus",
            "proposalstatus",
            "proposaltype",
        ):
            op.execute(sa.text(f"DROP TYPE IF EXISTS {enum_name}"))

    # ─────────────────────────────────────────────────────────
    # 2. Modify projects
    # ─────────────────────────────────────────────────────────
    with op.batch_alter_table("projects") as batch:
        batch.alter_column("max_concurrent_tasks", new_column_name="max_concurrent_runs")
        batch.add_column(sa.Column("bsage_workspace_id", sa.String(255), nullable=True))
        batch.add_column(sa.Column("bsupervisor_policy_id", sa.String(255), nullable=True))

    # ─────────────────────────────────────────────────────────
    # 3. Create new tables
    # ─────────────────────────────────────────────────────────

    # Create PG enum types via raw DDL (not SQLAlchemy Enum) so we control
    # creation timing. Column references use postgresql.ENUM(create_type=False)
    # so SQLAlchemy doesn't try to create them again.
    from sqlalchemy.dialects.postgresql import ENUM as PGEnum

    ENUM_DEFINITIONS = [
        ("requeststatus", ("open", "running", "completed", "abandoned")),
        ("runstatus", ("pending", "running", "blocked", "done")),
        ("runpriority", ("low", "medium", "high", "critical")),
        ("compositionsource", ("bsage", "local")),
        ("deliverabletype", ("code", "doc", "design", "data", "url")),
        ("deliverablestatus", ("draft", "ready", "delivered")),
        ("storagebackend", ("git", "object", "url")),
        ("activitylevel", ("milestone", "tool")),
    ]

    if is_postgres:
        for name, values in ENUM_DEFINITIONS:
            values_sql = ", ".join(f"'{v}'" for v in values)
            op.execute(sa.text(f"CREATE TYPE {name} AS ENUM ({values_sql})"))

    def _enum(name: str, *values: str):
        if is_postgres:
            return PGEnum(*values, name=name, create_type=False)
        return sa.Enum(*values, name=name)

    request_status = _enum("requeststatus", "open", "running", "completed", "abandoned")
    run_status = _enum("runstatus", "pending", "running", "blocked", "done")
    run_priority = _enum("runpriority", "low", "medium", "high", "critical")
    composition_source = _enum("compositionsource", "bsage", "local")
    deliverable_type = _enum("deliverabletype", "code", "doc", "design", "data", "url")
    deliverable_status = _enum("deliverablestatus", "draft", "ready", "delivered")
    storage_backend_enum = _enum("storagebackend", "git", "object", "url")
    activity_level = _enum("activitylevel", "milestone", "tool")

    # requests
    op.create_table(
        "requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("origin_message_id", sa.Uuid(), sa.ForeignKey("conversation_messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("intent_summary", sa.Text(), nullable=False),
        sa.Column("status", request_status, nullable=False, server_default="open"),
        sa.Column("user_confirmed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("superseded_by_id", sa.Uuid(), sa.ForeignKey("requests.id", ondelete="SET NULL"), nullable=True),
        sa.Column("composition_root_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_requests_project_status", "requests", ["project_id", "status"])
    op.create_index("ix_requests_tenant_created", "requests", ["tenant_id", "created_at"])

    # execution_runs (replaces tasks)
    op.create_table(
        "execution_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_id", sa.Uuid(), sa.ForeignKey("requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_run_id", sa.Uuid(), sa.ForeignKey("execution_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("composition_snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("status", run_status, nullable=False, server_default="pending"),
        sa.Column("priority", run_priority, nullable=False, server_default="medium"),
        sa.Column("output_type", sa.String(64), nullable=True),
        sa.Column("output_ref", sa.JSON(), nullable=True),
        sa.Column("estimated_cost_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("actual_cost_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("worker_id", sa.Uuid(), sa.ForeignKey("workers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("branch_name", sa.String(255), nullable=True),
        sa.Column("commit_hash", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_execution_runs_project_status", "execution_runs", ["project_id", "status"])
    op.create_index("ix_execution_runs_request", "execution_runs", ["request_id"])
    op.create_index("ix_execution_runs_tenant_created", "execution_runs", ["tenant_id", "created_at"])

    # composition_snapshots
    op.create_table(
        "composition_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_id", sa.Uuid(), sa.ForeignKey("requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("execution_run_id", sa.Uuid(), sa.ForeignKey("execution_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source", composition_source, nullable=False, server_default="local"),
        sa.Column("bsage_composition_id", sa.String(255), nullable=True),
        sa.Column("bsage_etag", sa.String(255), nullable=True),
        sa.Column("system_prompt_ref", sa.JSON(), nullable=False),
        sa.Column("tools_allowed", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("context_doc_refs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("persona_label", sa.String(255), nullable=False),
        sa.Column("fit_score", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_composition_snapshots_request", "composition_snapshots", ["request_id"])
    op.create_index("ix_composition_snapshots_tenant_created", "composition_snapshots", ["tenant_id", "created_at"])

    # Now attach the FKs we deferred (circular: requests.composition_root_id ↔ composition_snapshots.id;
    # execution_runs.composition_snapshot_id → composition_snapshots.id)
    op.create_foreign_key(
        "fk_requests_composition_root",
        "requests",
        "composition_snapshots",
        ["composition_root_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_execution_runs_composition_snapshot",
        "execution_runs",
        "composition_snapshots",
        ["composition_snapshot_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # execution_run_dependencies (M2M)
    op.create_table(
        "execution_run_dependencies",
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("execution_runs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("dependency_id", sa.Uuid(), sa.ForeignKey("execution_runs.id", ondelete="CASCADE"), primary_key=True),
    )

    # execution_run_history
    op.create_table(
        "execution_run_history",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("execution_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_status", run_status, nullable=False),
        sa.Column("to_status", run_status, nullable=False),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_execution_run_history_run_timestamp", "execution_run_history", ["run_id", "timestamp"])

    # execution_run_activities
    op.create_table(
        "execution_run_activities",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("execution_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("level", activity_level, nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_execution_run_activities_run_created", "execution_run_activities", ["run_id", "created_at"])
    op.create_index("ix_execution_run_activities_project_created", "execution_run_activities", ["project_id", "created_at"])

    # deliverables
    op.create_table(
        "deliverables",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_id", sa.Uuid(), sa.ForeignKey("requests.id", ondelete="SET NULL"), nullable=True),
        sa.Column("type", deliverable_type, nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("status", deliverable_status, nullable=False, server_default="draft"),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_deliverables_project_status", "deliverables", ["project_id", "status"])
    op.create_index("ix_deliverables_request", "deliverables", ["request_id"])

    # deliverable_versions
    op.create_table(
        "deliverable_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("deliverable_id", sa.Uuid(), sa.ForeignKey("deliverables.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_int", sa.Integer(), nullable=False),
        sa.Column("storage_backend", storage_backend_enum, nullable=False),
        sa.Column("content_ref", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("created_by_run_id", sa.Uuid(), sa.ForeignKey("execution_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_deliverable_versions_deliverable", "deliverable_versions", ["deliverable_id", "version_int"])

    # Circular: deliverables.current_version_id → deliverable_versions.id
    op.create_foreign_key(
        "fk_deliverables_current_version",
        "deliverables",
        "deliverable_versions",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # decisions
    op.create_table(
        "decisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_id", sa.Uuid(), sa.ForeignKey("requests.id", ondelete="SET NULL"), nullable=True),
        sa.Column("origin_run_id", sa.Uuid(), sa.ForeignKey("execution_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("blocking", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("resolved_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_decisions_project_resolved", "decisions", ["project_id", "resolved_at"])
    op.create_index("ix_decisions_tenant_blocking", "decisions", ["tenant_id", "blocking", "resolved_at"])

    # ─────────────────────────────────────────────────────────
    # 4. Add back modified columns to conversation_messages + cost_records
    # ─────────────────────────────────────────────────────────
    op.add_column(
        "conversation_messages",
        sa.Column(
            "request_id",
            sa.Uuid(),
            sa.ForeignKey("requests.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_conversation_messages_request", "conversation_messages", ["request_id"])

    op.add_column(
        "cost_records",
        sa.Column(
            "execution_run_id",
            sa.Uuid(),
            sa.ForeignKey("execution_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_cost_records_tenant_recorded", "cost_records", ["tenant_id", "recorded_at"])


def downgrade() -> None:
    """Downgrade is destructive-equivalent: the old schema can't be
    reconstructed with full fidelity (agents/tasks/phases data is gone).

    For beta, downgrade drops the new schema without restoring the old
    one. In practice, we restore from backup instead.
    """
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    op.drop_index("ix_cost_records_tenant_recorded", table_name="cost_records")
    with op.batch_alter_table("cost_records") as batch:
        batch.drop_column("execution_run_id")

    op.drop_index("ix_conversation_messages_request", table_name="conversation_messages")
    with op.batch_alter_table("conversation_messages") as batch:
        batch.drop_column("request_id")

    op.drop_constraint("fk_deliverables_current_version", "deliverables", type_="foreignkey")
    op.drop_table("decisions")
    op.drop_index("ix_deliverable_versions_deliverable", table_name="deliverable_versions")
    op.drop_table("deliverable_versions")
    op.drop_index("ix_deliverables_request", table_name="deliverables")
    op.drop_index("ix_deliverables_project_status", table_name="deliverables")
    op.drop_table("deliverables")
    op.drop_index("ix_execution_run_activities_project_created", table_name="execution_run_activities")
    op.drop_index("ix_execution_run_activities_run_created", table_name="execution_run_activities")
    op.drop_table("execution_run_activities")
    op.drop_index("ix_execution_run_history_run_timestamp", table_name="execution_run_history")
    op.drop_table("execution_run_history")
    op.drop_table("execution_run_dependencies")
    op.drop_constraint("fk_execution_runs_composition_snapshot", "execution_runs", type_="foreignkey")
    op.drop_constraint("fk_requests_composition_root", "requests", type_="foreignkey")
    op.drop_index("ix_composition_snapshots_tenant_created", table_name="composition_snapshots")
    op.drop_index("ix_composition_snapshots_request", table_name="composition_snapshots")
    op.drop_table("composition_snapshots")
    op.drop_index("ix_execution_runs_tenant_created", table_name="execution_runs")
    op.drop_index("ix_execution_runs_request", table_name="execution_runs")
    op.drop_index("ix_execution_runs_project_status", table_name="execution_runs")
    op.drop_table("execution_runs")
    op.drop_index("ix_requests_tenant_created", table_name="requests")
    op.drop_index("ix_requests_project_status", table_name="requests")
    op.drop_table("requests")

    with op.batch_alter_table("projects") as batch:
        batch.drop_column("bsupervisor_policy_id")
        batch.drop_column("bsage_workspace_id")
        batch.alter_column("max_concurrent_runs", new_column_name="max_concurrent_tasks")

    if is_postgres:
        for enum_name in (
            "activitylevel",
            "storagebackend",
            "deliverablestatus",
            "deliverabletype",
            "compositionsource",
            "runpriority",
            "runstatus",
            "requeststatus",
        ):
            op.execute(sa.text(f"DROP TYPE IF EXISTS {enum_name}"))
