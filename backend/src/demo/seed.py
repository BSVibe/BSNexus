"""BSNexus demo data seeding for a single ephemeral tenant.

Populates a realistic snapshot so the visitor's dashboard renders
immediately:

- 1 demo project
- 5 conversation messages (mix of chit_chat / question / request)
- 2 Requests (one with active ExecutionRun)
- 1 ExecutionRun with status=running, plan tree of 3 phases / 12 tasks
- 1 Decision (blocking, awaiting approval)
- 2 Deliverables

Called by ``DemoSessionServiceSqlAlchemy`` after the new tenant row is
inserted, within the same transaction.
"""

from __future__ import annotations

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
    await session.execute(
        text(
            "INSERT INTO projects (id, tenant_id, name, mission, status, "
            "created_at, updated_at) "
            "VALUES (:id, :tid, :name, :mission, 'active', :created, :updated)"
        ),
        {
            "id": project_id,
            "tid": tenant_id,
            "name": "Launch landing page redesign",
            "mission": "Modernize the marketing site with a unified design system "
                       "and ship a new public demo of all 4 BSVibe products.",
            "created": now - timedelta(days=3),
            "updated": now - timedelta(hours=2),
        },
    )

    # ─── Conversation messages ─────────────────────────────────────────
    sample_messages = [
        ("user", "Hey, I want to redesign the landing page", now - timedelta(days=3)),
        ("assistant", "Great. What's the primary goal — conversion, brand, or "
                      "product clarity?", now - timedelta(days=3, minutes=-1)),
        ("user", "Conversion. Visitors aren't getting past the hero section.",
         now - timedelta(days=2, hours=18)),
        ("user", "Also need a public demo so people can try without signing up",
         now - timedelta(days=1, hours=4)),
        ("assistant", "Drafting a plan with two parallel tracks: hero rewrite + "
                      "demo stack. Will share the run.",
         now - timedelta(days=1, hours=3, minutes=-58)),
    ]
    for sender, body, ts in sample_messages:
        await session.execute(
            text(
                "INSERT INTO conversation_messages "
                "(id, tenant_id, project_id, sender, body, created_at) "
                "VALUES (:id, :tid, :pid, :sender, :body, :created)"
            ),
            {
                "id": _uuid.uuid4(),
                "tid": tenant_id,
                "pid": project_id,
                "sender": sender,
                "body": body,
                "created": ts,
            },
        )

    # ─── Requests ──────────────────────────────────────────────────────
    request_ids = [_uuid.uuid4() for _ in range(2)]
    for idx, req_id in enumerate(request_ids):
        await session.execute(
            text(
                "INSERT INTO requests (id, tenant_id, project_id, summary, "
                "status, created_at, updated_at) "
                "VALUES (:id, :tid, :pid, :summary, :status, :created, :updated)"
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
                "status": "in_progress" if idx == 0 else "open",
                "created": now - timedelta(days=2 - idx),
                "updated": now - timedelta(hours=4 - idx),
            },
        )

    # ─── ExecutionRun (status=done — avoids the prod orchestrator
    #     trying to dispatch when no real executor_config exists) ─────
    run_id = _uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO execution_runs (id, tenant_id, project_id, "
            "request_id, status, prompt, created_at, updated_at) "
            "VALUES (:id, :tid, :pid, :rid, 'done', :prompt, :created, :updated)"
        ),
        {
            "id": run_id,
            "tid": tenant_id,
            "pid": project_id,
            "rid": request_ids[0],
            "prompt": "Rewrite the hero section with a stronger conversion CTA. "
                      "Use the BSVibe design system tokens.",
            "created": now - timedelta(hours=6),
            "updated": now - timedelta(minutes=30),
        },
    )

    # ─── Deliverables ──────────────────────────────────────────────────
    for idx, title in enumerate(["Hero copy v2", "New screenshot set"]):
        await session.execute(
            text(
                "INSERT INTO deliverables (id, tenant_id, project_id, "
                "request_id, title, kind, status, created_at, updated_at) "
                "VALUES (:id, :tid, :pid, :rid, :title, 'note', "
                "'completed', :created, :updated)"
            ),
            {
                "id": _uuid.uuid4(),
                "tid": tenant_id,
                "pid": project_id,
                "rid": request_ids[idx],
                "title": title,
                "created": now - timedelta(hours=8 - idx),
                "updated": now - timedelta(hours=4 - idx),
            },
        )

    # ─── Decision (blocking, awaiting approval) ───────────────────────
    await session.execute(
        text(
            "INSERT INTO decisions (id, tenant_id, project_id, run_id, "
            "title, body, status, blocking, created_at, updated_at) "
            "VALUES (:id, :tid, :pid, :run_id, :title, :body, 'open', TRUE, "
            ":created, :updated)"
        ),
        {
            "id": _uuid.uuid4(),
            "tid": tenant_id,
            "pid": project_id,
            "run_id": run_id,
            "title": "Approve the new hero copy variant",
            "body": "The new hero leads with the demo CTA. Want approval before we "
                    "publish to production.",
            "created": now - timedelta(hours=2),
            "updated": now - timedelta(hours=2),
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
