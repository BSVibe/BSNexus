"""BSNexus demo data seeding for a single ephemeral tenant.

Populates a realistic snapshot so the visitor's dashboard renders
immediately:

- 1 demo project (status=active)
- 5 conversation messages (user / assistant alternating)
- 2 Requests (one open, one running)
- 1 ExecutionRun with status=done (avoids prod orchestrator dispatch)
- 2 Deliverables (delivered)
- 1 Decision (blocking, awaiting approval)

Called by ``DemoSessionServiceSqlAlchemy`` after the new tenant row is
inserted, within the same transaction.
"""

from __future__ import annotations

import json
import uuid as _uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


async def seed_demo(*, tenant_id: UUID, session: AsyncSession) -> None:
    """Populate BSNexus demo data for ``tenant_id``."""
    project_id = _uuid.uuid4()
    now = datetime.now(UTC)

    # ─── Project ───────────────────────────────────────────────────────
    # Schema: id, name, description (NOT NULL), status (projectstatus enum),
    # tenant_id, max_concurrent_runs, workspace_type (workspacetype enum),
    # created_at, updated_at.
    await session.execute(
        text(
            "INSERT INTO projects (id, tenant_id, name, description, "
            "status, workspace_type, created_at, updated_at) "
            "VALUES (:id, :tid, :name, :description, 'active', "
            "'server_managed', :created, :updated)"
        ),
        {
            "id": project_id,
            "tid": tenant_id,
            "name": "Launch landing page redesign",
            "description": (
                "Modernize the marketing site with a unified design system "
                "and ship a new public demo of all 4 BSVibe products."
            ),
            "created": now - timedelta(days=3),
            "updated": now - timedelta(hours=2),
        },
    )

    # ─── Conversation messages ─────────────────────────────────────────
    # Schema: id, project_id, role, content, actions (json default '[]'),
    # source, created_at. No tenant_id (scoped via project_id FK).
    sample_messages = [
        ("user", "Hey, I want to redesign the landing page", now - timedelta(days=3)),
        (
            "assistant",
            "Great. What's the primary goal — conversion, brand, or product clarity?",
            now - timedelta(days=3, minutes=-1),
        ),
        (
            "user",
            "Conversion. Visitors aren't getting past the hero section.",
            now - timedelta(days=2, hours=18),
        ),
        (
            "user",
            "Also need a public demo so people can try without signing up",
            now - timedelta(days=1, hours=4),
        ),
        (
            "assistant",
            "Drafting a plan with two parallel tracks: hero rewrite + demo stack. "
            "Will share the run.",
            now - timedelta(days=1, hours=3, minutes=-58),
        ),
    ]
    for role, content, ts in sample_messages:
        await session.execute(
            text(
                "INSERT INTO conversation_messages "
                "(id, project_id, role, content, source, created_at) "
                "VALUES (:id, :pid, :role, :content, 'web', :created)"
            ),
            {
                "id": _uuid.uuid4(),
                "pid": project_id,
                "role": role,
                "content": content,
                "created": ts,
            },
        )

    # ─── Requests ──────────────────────────────────────────────────────
    # Schema: id, tenant_id, project_id, intent_summary (NOT NULL), status
    # (requeststatus enum: open/running/completed/abandoned), user_confirmed,
    # created_at, updated_at.
    request_ids = [_uuid.uuid4() for _ in range(2)]
    for idx, req_id in enumerate(request_ids):
        await session.execute(
            text(
                "INSERT INTO requests (id, tenant_id, project_id, "
                "intent_summary, status, user_confirmed, created_at, "
                "updated_at) "
                "VALUES (:id, :tid, :pid, :summary, :status, TRUE, "
                ":created, :updated)"
            ),
            {
                "id": req_id,
                "tid": tenant_id,
                "pid": project_id,
                "summary": (
                    "Rewrite the hero section with a stronger CTA"
                    if idx == 0
                    else "Build the public interactive demo for 4 products"
                ),
                "status": "running" if idx == 0 else "open",
                "created": now - timedelta(days=2 - idx),
                "updated": now - timedelta(hours=4 - idx),
            },
        )

    # ─── ExecutionRun (status=done — avoids prod orchestrator dispatch) ─
    # Schema: id, tenant_id, project_id, request_id, status (runstatus
    # enum: pending/running/blocked/done), priority (runpriority enum),
    # directive (NOT prompt — that column doesn't exist),
    # created_at, updated_at.
    run_id = _uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO execution_runs (id, tenant_id, project_id, "
            "request_id, status, priority, directive, "
            "created_at, updated_at) "
            "VALUES (:id, :tid, :pid, :rid, 'done', 'medium', :directive, "
            ":created, :updated)"
        ),
        {
            "id": run_id,
            "tid": tenant_id,
            "pid": project_id,
            "rid": request_ids[0],
            "directive": (
                "Rewrite the hero section with a stronger conversion CTA. "
                "Use the BSVibe design system tokens."
            ),
            "created": now - timedelta(hours=6),
            "updated": now - timedelta(minutes=30),
        },
    )

    # ─── Deliverables ──────────────────────────────────────────────────
    # Schema: id, tenant_id, project_id, request_id, type (deliverabletype
    # enum: code/doc/design/data/url — not 'note'), title, status
    # (deliverablestatus enum: draft/ready/delivered — not 'completed'),
    # created_at, updated_at.
    for idx, title in enumerate(["Hero copy v2", "New screenshot set"]):
        await session.execute(
            text(
                "INSERT INTO deliverables (id, tenant_id, project_id, "
                "request_id, type, title, status, created_at, updated_at) "
                "VALUES (:id, :tid, :pid, :rid, :dtype, :title, 'delivered', "
                ":created, :updated)"
            ),
            {
                "id": _uuid.uuid4(),
                "tid": tenant_id,
                "pid": project_id,
                "rid": request_ids[idx],
                "dtype": "doc" if idx == 0 else "design",
                "title": title,
                "created": now - timedelta(hours=8 - idx),
                "updated": now - timedelta(hours=4 - idx),
            },
        )

    # ─── Decision (blocking, awaiting approval) ───────────────────────
    # Schema: id, tenant_id, project_id, request_id, origin_run_id,
    # question (NOT NULL — not title/body), options (json default '[]'),
    # blocking, resolved_at, resolution, resolved_by, created_at.
    await session.execute(
        text(
            "INSERT INTO decisions (id, tenant_id, project_id, "
            "origin_run_id, question, options, blocking, created_at) "
            "VALUES (:id, :tid, :pid, :run_id, :question, "
            "CAST(:options AS json), TRUE, :created)"
        ),
        {
            "id": _uuid.uuid4(),
            "tid": tenant_id,
            "pid": project_id,
            "run_id": run_id,
            "question": "Approve the new hero copy variant?",
            "options": json.dumps(
                [
                    {"label": "Approve", "value": "approve"},
                    {"label": "Request changes", "value": "revise"},
                ]
            ),
            "created": now - timedelta(hours=2),
        },
    )

    logger.info(
        "demo_seed_complete",
        tenant_id=str(tenant_id),
        project_id=str(project_id),
        messages=len(sample_messages),
        requests=len(request_ids),
        deliverables=2,
        decisions=1,
    )
