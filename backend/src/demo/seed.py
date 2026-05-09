"""BSNexus demo data seeding for a single ephemeral tenant.

G0 backend reset removed conversation/run-summary tables. Demo seed data now
uses the greenfield contract directly: projects, directions, requests,
deliverables, and decisions.
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


async def _insert_project(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID,
    name: str,
    description: str,
    created_at: datetime,
    updated_at: datetime,
) -> None:
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
            "name": name,
            "description": description,
            "created": created_at,
            "updated": updated_at,
        },
    )


async def _insert_direction(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID,
    body: str,
    created_at: datetime,
) -> UUID:
    direction_id = _uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO directions (id, tenant_id, project_id, source, actor_id, body, created_at) "
            "VALUES (:id, :tid, :pid, 'web', 'demo-user', :body, :created)"
        ),
        {
            "id": direction_id,
            "tid": tenant_id,
            "pid": project_id,
            "body": body,
            "created": created_at,
        },
    )
    return direction_id


async def _insert_request(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID,
    origin_direction_id: UUID,
    intent: str,
    status: str,
    created_at: datetime,
    updated_at: datetime,
) -> UUID:
    request_id = _uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO requests (id, tenant_id, project_id, origin_direction_id, intent, "
            "status, created_at, updated_at) "
            "VALUES (:id, :tid, :pid, :direction_id, :intent, :status, :created, :updated)"
        ),
        {
            "id": request_id,
            "tid": tenant_id,
            "pid": project_id,
            "direction_id": origin_direction_id,
            "intent": intent,
            "status": status,
            "created": created_at,
            "updated": updated_at,
        },
    )
    return request_id


async def _insert_deliverable(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID,
    request_id: UUID,
    deliverable_type: str,
    title: str,
    summary: str,
    status: str,
    proof_state: str,
    created_at: datetime,
    updated_at: datetime,
) -> None:
    await session.execute(
        text(
            "INSERT INTO deliverables (id, tenant_id, project_id, request_id, type, title, "
            "summary, artifact_refs, status, proof_state, created_at, updated_at) "
            "VALUES (:id, :tid, :pid, :rid, :dtype, :title, :summary, "
            "CAST(:artifact_refs AS json), :status, :proof_state, :created, :updated)"
        ),
        {
            "id": _uuid.uuid4(),
            "tid": tenant_id,
            "pid": project_id,
            "rid": request_id,
            "dtype": deliverable_type,
            "title": title,
            "summary": summary,
            "artifact_refs": json.dumps([]),
            "status": status,
            "proof_state": proof_state,
            "created": created_at,
            "updated": updated_at,
        },
    )


async def seed_demo(*, tenant_id: UUID, session: AsyncSession) -> None:
    """Populate BSNexus demo data for ``tenant_id``."""
    now = datetime.now(UTC)
    project_id = _uuid.uuid4()
    project_id_2 = _uuid.uuid4()

    await _insert_project(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        name="Launch landing page redesign",
        description=(
            "Modernize the marketing site with a unified design system "
            "and ship a new public demo of all 4 BSVibe products."
        ),
        created_at=now - timedelta(days=3),
        updated_at=now - timedelta(hours=2),
    )
    await _insert_project(
        session,
        tenant_id=tenant_id,
        project_id=project_id_2,
        name="Customer onboarding playbook",
        description=(
            "Codify the first-7-day customer journey into a playbook "
            "the agent can follow autonomously."
        ),
        created_at=now - timedelta(days=7),
        updated_at=now - timedelta(days=1),
    )

    hero_direction_id = await _insert_direction(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        body="Rewrite the landing page hero with a stronger conversion CTA.",
        created_at=now - timedelta(days=3),
    )
    demo_direction_id = await _insert_direction(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        body="Build the public interactive demo for all four products.",
        created_at=now - timedelta(days=1, hours=4),
    )
    onboarding_direction_id = await _insert_direction(
        session,
        tenant_id=tenant_id,
        project_id=project_id_2,
        body="Draft the v1 onboarding playbook with day-by-day actions.",
        created_at=now - timedelta(days=7),
    )

    hero_request_id = await _insert_request(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        origin_direction_id=hero_direction_id,
        intent="Rewrite the hero section with a stronger CTA",
        status="running",
        created_at=now - timedelta(days=2),
        updated_at=now - timedelta(hours=4),
    )
    demo_request_id = await _insert_request(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        origin_direction_id=demo_direction_id,
        intent="Build the public interactive demo for 4 products",
        status="open",
        created_at=now - timedelta(days=1),
        updated_at=now - timedelta(hours=3),
    )
    onboarding_request_id = await _insert_request(
        session,
        tenant_id=tenant_id,
        project_id=project_id_2,
        origin_direction_id=onboarding_direction_id,
        intent="Draft the v1 onboarding playbook with day-by-day actions",
        status="shipped",
        created_at=now - timedelta(days=6),
        updated_at=now - timedelta(days=1, hours=4),
    )

    await _insert_deliverable(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        request_id=hero_request_id,
        deliverable_type="doc",
        title="Hero copy v2",
        summary="Conversion-focused hero copy and CTA hierarchy.",
        status="review_ready",
        proof_state="human_review_required",
        created_at=now - timedelta(hours=8),
        updated_at=now - timedelta(hours=4),
    )
    await _insert_deliverable(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        request_id=demo_request_id,
        deliverable_type="design",
        title="New screenshot set",
        summary="Demo-ready screenshots for the product tour.",
        status="draft",
        proof_state="verification_missing",
        created_at=now - timedelta(hours=7),
        updated_at=now - timedelta(hours=3),
    )
    await _insert_deliverable(
        session,
        tenant_id=tenant_id,
        project_id=project_id_2,
        request_id=onboarding_request_id,
        deliverable_type="doc",
        title="Onboarding playbook v1",
        summary="Seven-day onboarding flow for first customer activation.",
        status="shipped",
        proof_state="verified",
        created_at=now - timedelta(days=2),
        updated_at=now - timedelta(days=1, hours=4),
    )

    decisions = [
        {
            "request_id": hero_request_id,
            "question": "Approve the new hero copy variant?",
            "options": ["Approve", "Request changes"],
            "blocking": True,
            "resolved_at": None,
            "resolution": None,
            "resolved_by": None,
            "created": now - timedelta(hours=2),
        },
        {
            "request_id": demo_request_id,
            "question": "Pick the primary CTA color for the demo banner",
            "options": ["Brand blue", "High-contrast amber", "Subtle slate"],
            "blocking": False,
            "resolved_at": now - timedelta(hours=5),
            "resolution": "Brand blue",
            "resolved_by": "demo-user",
            "created": now - timedelta(hours=18),
        },
    ]
    for decision in decisions:
        await session.execute(
            text(
                "INSERT INTO decisions (id, tenant_id, project_id, request_id, question, options, "
                "blocking, resolved_at, resolution, resolved_by, created_at) "
                "VALUES (:id, :tid, :pid, :rid, :question, CAST(:options AS json), "
                ":blocking, :resolved_at, :resolution, :resolved_by, :created)"
            ),
            {
                "id": _uuid.uuid4(),
                "tid": tenant_id,
                "pid": project_id,
                "rid": decision["request_id"],
                "question": decision["question"],
                "options": json.dumps(decision["options"]),
                "blocking": decision["blocking"],
                "resolved_at": decision["resolved_at"],
                "resolution": decision["resolution"],
                "resolved_by": decision["resolved_by"],
                "created": decision["created"],
            },
        )

    logger.info(
        "demo_seed_complete",
        tenant_id=str(tenant_id),
        projects=2,
        directions=3,
        requests=3,
        deliverables=3,
        decisions=len(decisions),
    )
