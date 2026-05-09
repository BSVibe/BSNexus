"""greenfield backend reset g0

Revision ID: p0q1r2s3t4u5
Revises: n6g7h8i9j0k1
Create Date: 2026-05-09 23:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "p0q1r2s3t4u5"
down_revision: str | Sequence[str] | None = "n6g7h8i9j0k1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _drop_table_if_exists(name: str) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(sa.text(f'DROP TABLE IF EXISTS "{name}" CASCADE'))
    else:
        op.drop_table(name)


def upgrade() -> None:
    for table_name in (
        "run_summaries",
        "execution_run_history",
        "execution_run_activities",
        "execution_run_dependencies",
        "execution_runs",
        "deliverable_versions",
        "deliverables",
        "decisions",
        "composition_snapshots",
        "requests",
        "conversation_messages",
        "executor_configs",
    ):
        _drop_table_if_exists(table_name)

    op.create_table(
        "directions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source", sa.Enum("web", "mobile_web", "slack", "email", "cli", "voice", name="direction_source"), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("target_hint", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_directions_tenant_created", "directions", ["tenant_id", "created_at"])
    op.create_index("ix_directions_project_created", "directions", ["project_id", "created_at"])

    op.create_table(
        "requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("origin_direction_id", sa.Uuid(), sa.ForeignKey("directions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("intent", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("open", "running", "blocked", "review_ready", "shipped", "abandoned", name="request_status"),
            nullable=False,
            server_default="open",
        ),
        sa.Column("current_step_id", sa.Uuid(), nullable=True),
        sa.Column("last_brief_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_requests_project_status", "requests", ["project_id", "status"])
    op.create_index("ix_requests_tenant_created", "requests", ["tenant_id", "created_at"])

    op.create_table(
        "work_plans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("request_id", sa.Uuid(), sa.ForeignKey("requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Enum("system", "llm_assisted", "user", name="work_plan_created_by"), nullable=False),
        sa.Column("status", sa.Enum("draft", "active", "superseded", "completed", name="work_plan_status"), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_work_plans_request_version", "work_plans", ["request_id", "version"], unique=True)

    op.create_table(
        "work_steps",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("request_id", sa.Uuid(), sa.ForeignKey("requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("work_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("expected_outputs", sa.JSON(), nullable=False),
        sa.Column("verifier_policy", sa.JSON(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("pending", "running", "needs_decision", "verifying", "review_ready", "failed", "skipped", name="work_step_status"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_work_steps_request_status", "work_steps", ["request_id", "status"])

    op.create_table(
        "run_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("work_step_id", sa.Uuid(), sa.ForeignKey("work_steps.id", ondelete="CASCADE"), nullable=False),
        sa.Column("executor_kind", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("phase", sa.Enum("prepare", "work", "verify", "summarize", "terminal", name="run_attempt_phase"), nullable=False, server_default="prepare"),
        sa.Column("status", sa.Enum("running", "completed", "failed", "timed_out", name="run_attempt_status"), nullable=False, server_default="running"),
        sa.Column("round_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("terminal_reason", sa.Text(), nullable=True),
        sa.Column("telemetry", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_run_attempts_work_step", "run_attempts", ["work_step_id", "started_at"])

    op.create_table(
        "tool_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_attempt_id", sa.Uuid(), sa.ForeignKey("run_attempts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("round_index", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.Text(), nullable=False),
        sa.Column("args_hash", sa.Text(), nullable=False),
        sa.Column("args_summary", sa.Text(), nullable=True),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("writes", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_tool_events_run_round", "tool_events", ["run_attempt_id", "round_index"])

    op.create_table(
        "proof_policies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("deliverable_type", sa.Enum("code", "pr", "preview", "design", "doc", "data", "marketing", "report", name="proof_policy_deliverable_type"), nullable=False),
        sa.Column("verifier_type", sa.Text(), nullable=False),
        sa.Column("command_template", sa.JSON(), nullable=True),
        sa.Column("required_refs", sa.JSON(), nullable=False),
        sa.Column("timeout_s", sa.Integer(), nullable=False),
        sa.Column("pass_condition", sa.Text(), nullable=False),
    )

    op.create_table(
        "deliverables",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_id", sa.Uuid(), sa.ForeignKey("requests.id", ondelete="SET NULL"), nullable=True),
        sa.Column("work_step_id", sa.Uuid(), sa.ForeignKey("work_steps.id", ondelete="SET NULL"), nullable=True),
        sa.Column("type", sa.Enum("code", "pr", "preview", "design", "doc", "data", "marketing", "report", name="deliverable_type"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("artifact_refs", sa.JSON(), nullable=False),
        sa.Column(
            "proof_state",
            sa.Enum("verification_missing", "verifying", "verified", "verification_failed", "human_review_required", name="proof_state"),
            nullable=False,
            server_default="verification_missing",
        ),
        sa.Column("proof_policy_id", sa.Uuid(), sa.ForeignKey("proof_policies.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.Enum("draft", "verifying", "review_ready", "shipped", "rejected", name="deliverable_status"), nullable=False, server_default="draft"),
        sa.Column("risk_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_deliverables_project_status", "deliverables", ["project_id", "status"])
    op.create_index("ix_deliverables_request", "deliverables", ["request_id"])
    op.create_index("ix_deliverables_project_proof_state", "deliverables", ["project_id", "proof_state"])

    op.create_table(
        "proof_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("deliverable_id", sa.Uuid(), sa.ForeignKey("deliverables.id", ondelete="CASCADE"), nullable=False),
        sa.Column("verifier_type", sa.Text(), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("status", sa.Enum("queued", "running", "verified", "failed", "human_review_required", name="proof_attempt_status"), nullable=False, server_default="queued"),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("proof_summary", sa.Text(), nullable=True),
        sa.Column("proof_refs", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_proof_attempts_deliverable_created", "proof_attempts", ["deliverable_id", "created_at"])

    op.create_table(
        "decisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_id", sa.Uuid(), sa.ForeignKey("requests.id", ondelete="SET NULL"), nullable=True),
        sa.Column("work_step_id", sa.Uuid(), sa.ForeignKey("work_steps.id", ondelete="SET NULL"), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("blocking", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("resolved_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_decisions_project_resolved", "decisions", ["project_id", "resolved_at"])
    op.create_index("ix_decisions_tenant_blocking", "decisions", ["tenant_id", "blocking", "resolved_at"])

    op.create_table(
        "brief_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scope", sa.Enum("company", "project", "request", name="brief_scope"), nullable=False),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True),
        sa.Column("request_id", sa.Uuid(), sa.ForeignKey("requests.id", ondelete="CASCADE"), nullable=True),
        sa.Column("sections", sa.JSON(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_brief_snapshots_scope_generated", "brief_snapshots", ["tenant_id", "scope", "generated_at"])


def downgrade() -> None:
    raise NotImplementedError("Greenfield reset is not backward-compatible.")
