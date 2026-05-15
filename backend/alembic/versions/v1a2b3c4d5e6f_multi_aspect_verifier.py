"""Multi-aspect verifier — replace ProofPolicy/ProofAttempt with VerificationAspect

Revision ID: v1a2b3c4d5e6f
Revises: cb713805c963
Create Date: 2026-05-15 16:00:00.000000

Phase 1 of the C-shape verifier rollout. ``proof_attempts`` was
1:1 with deliverable (one row per verification result). The new
``verification_aspects`` is N:1 — a deliverable can have N aspects
(test, lint, install_smoke today; build, external_audit, knowledge
later) and ``Deliverable.proof_state`` rolls up from the aspect
statuses.

This migration:
  - creates ``proof_aspect_type`` + ``proof_aspect_status`` enums
  - creates ``verification_aspects`` table
  - backfills every existing ``proof_attempts`` row as a
    ``code_test`` aspect (status mapped 1:1)
  - drops ``proof_attempts`` table + ``proof_attempt_status`` enum
  - drops ``proof_policies`` table + ``proof_policy_deliverable_type``
    enum (the table was a transient row catalog, not durable state —
    retire is safe)
  - drops ``deliverables.proof_policy_id`` FK column

Downgrade is supported but the verification_aspects → proof_attempts
roll-back loses any aspect that wasn't ``code_test`` (lint /
install_smoke / future aspects). Rolling back after non-code_test
aspects have been used is a one-way data loss; the downgrade is for
quick reverts in the deploy window.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "v1a2b3c4d5e6f"
down_revision: Union[str, Sequence[str], None] = "cb713805c963"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ASPECT_TYPE_VALUES = ("code_test", "code_lint", "code_install_smoke")
_ASPECT_STATUS_VALUES = ("queued", "running", "passed", "failed", "skipped", "error")


def upgrade() -> None:
    # 1. enums
    aspect_type = sa.Enum(*_ASPECT_TYPE_VALUES, name="proof_aspect_type")
    aspect_status = sa.Enum(*_ASPECT_STATUS_VALUES, name="proof_aspect_status")
    aspect_type.create(op.get_bind(), checkfirst=True)
    aspect_status.create(op.get_bind(), checkfirst=True)

    # 2. verification_aspects table
    op.create_table(
        "verification_aspects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "deliverable_id",
            sa.Uuid(),
            sa.ForeignKey("deliverables.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("aspect_type", aspect_type, nullable=False),
        sa.Column(
            "status",
            aspect_status,
            nullable=False,
            server_default="queued",
        ),
        sa.Column("inputs", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("blocking", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("attempt_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_verification_aspects_deliverable",
        "verification_aspects",
        ["deliverable_id", "created_at"],
    )
    op.create_index(
        "ix_verification_aspects_status",
        "verification_aspects",
        ["status"],
    )

    # 3. backfill from proof_attempts. Status mapping:
    #    verified → passed, failed → failed, queued → queued,
    #    running → running, human_review_required → skipped (these
    #    were "no policy matched" placeholders; deliverable.proof_state
    #    already carries the correct human_review_required signal).
    op.execute(
        """
        INSERT INTO verification_aspects
            (id, deliverable_id, aspect_type, status, inputs,
             blocking, result_summary, exit_code, attempt_no,
             created_at, completed_at)
        SELECT
            gen_random_uuid(),
            deliverable_id,
            'code_test',
            CASE status::text
                WHEN 'verified' THEN 'passed'::proof_aspect_status
                WHEN 'failed' THEN 'failed'::proof_aspect_status
                WHEN 'queued' THEN 'queued'::proof_aspect_status
                WHEN 'running' THEN 'running'::proof_aspect_status
                WHEN 'human_review_required' THEN 'skipped'::proof_aspect_status
            END,
            COALESCE(inputs, '{}'::json),
            true,
            proof_summary,
            exit_code,
            1,
            created_at,
            completed_at
        FROM proof_attempts
        """
    )

    # 4. drop ``Deliverable.proof_policy_id`` (FK to proof_policies, soon
    # dropped). The column was server-managed and never user-set.
    op.drop_constraint("deliverables_proof_policy_id_fkey", "deliverables", type_="foreignkey")
    op.drop_column("deliverables", "proof_policy_id")

    # 5. drop old tables
    op.drop_index("ix_proof_attempts_deliverable_created", table_name="proof_attempts")
    op.drop_table("proof_attempts")
    op.drop_table("proof_policies")

    # 6. drop old enums (PG keeps them until manually dropped)
    sa.Enum(name="proof_attempt_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="proof_policy_deliverable_type").drop(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    # Recreate old enums + tables, then roll the code_test aspects back
    # as proof_attempts. Non-code_test aspects (lint / install_smoke)
    # are dropped — this is intentional and documented above.
    proof_attempt_status = sa.Enum(
        "queued", "running", "verified", "failed", "human_review_required",
        name="proof_attempt_status",
    )
    proof_policy_deliverable_type = sa.Enum(
        "code", "doc", "design", "data", "pr", "preview",
        name="proof_policy_deliverable_type",
    )
    proof_attempt_status.create(op.get_bind(), checkfirst=True)
    proof_policy_deliverable_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "proof_policies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("deliverable_type", proof_policy_deliverable_type, nullable=False),
        sa.Column("verifier_type", sa.Text(), nullable=False),
        sa.Column("command_template", sa.JSON(), nullable=True),
        sa.Column("required_refs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("timeout_s", sa.Integer(), nullable=False),
        sa.Column("pass_condition", sa.Text(), nullable=False),
    )
    op.create_table(
        "proof_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "deliverable_id",
            sa.Uuid(),
            sa.ForeignKey("deliverables.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("verifier_type", sa.Text(), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("status", proof_attempt_status, nullable=False, server_default="queued"),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("proof_summary", sa.Text(), nullable=True),
        sa.Column("proof_refs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_proof_attempts_deliverable_created",
        "proof_attempts",
        ["deliverable_id", "created_at"],
    )

    op.add_column(
        "deliverables",
        sa.Column("proof_policy_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "deliverables_proof_policy_id_fkey",
        "deliverables",
        "proof_policies",
        ["proof_policy_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.execute(
        """
        INSERT INTO proof_attempts
            (id, deliverable_id, verifier_type, inputs, status,
             exit_code, proof_summary, proof_refs, created_at,
             completed_at)
        SELECT
            id,
            deliverable_id,
            'python_test',
            COALESCE(inputs, '{}'::json),
            CASE status::text
                WHEN 'passed' THEN 'verified'::proof_attempt_status
                WHEN 'failed' THEN 'failed'::proof_attempt_status
                WHEN 'queued' THEN 'queued'::proof_attempt_status
                WHEN 'running' THEN 'running'::proof_attempt_status
                WHEN 'skipped' THEN 'human_review_required'::proof_attempt_status
                WHEN 'error' THEN 'failed'::proof_attempt_status
            END,
            exit_code,
            result_summary,
            '[]'::json,
            created_at,
            completed_at
        FROM verification_aspects
        WHERE aspect_type = 'code_test'
        """
    )

    op.drop_index("ix_verification_aspects_status", table_name="verification_aspects")
    op.drop_index("ix_verification_aspects_deliverable", table_name="verification_aspects")
    op.drop_table("verification_aspects")
    sa.Enum(name="proof_aspect_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="proof_aspect_type").drop(op.get_bind(), checkfirst=True)
