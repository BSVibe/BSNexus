"""Brief endpoint contract — `/api/v1/brief` (decision-locks A2)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend.src.models import (
    Decision,
    Deliverable,
    DeliverableStatus,
    DeliverableType,
    ExecutionRun,
    Project,
    ProofState,
    Request,
    RequestStatus,
    RunStatus,
    Tenant,
)


async def _make_project(client, name: str = "Proj") -> str:
    resp = await client.post(
        "/api/v1/projects",
        json={"name": name},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


# ─── Empty / shape ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_brief_empty_project_returns_all_sections(client) -> None:
    pid = await _make_project(client)
    resp = await client.get(
        f"/api/v1/brief?project_id={pid}",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["project_id"] == pid
    assert body["shipped"] == []
    assert body["needs_decision"] == []
    assert body["blocked"] == []
    assert body["running"] == []
    assert body["next"] == []
    assert "generated_at" in body


@pytest.mark.asyncio
async def test_brief_company_shape_when_project_id_omitted(client) -> None:
    resp = await client.get("/api/v1/brief", headers={"Authorization": "Bearer fake"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["project_id"] is None


# ─── Section content ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_brief_shipped_returns_only_verified_deliverables(
    client, db_session, mock_tenant_id
) -> None:
    pid = await _make_project(client)
    pid_uuid = uuid.UUID(pid)
    now = datetime.now(timezone.utc)

    db_session.add_all(
        [
            Deliverable(
                tenant_id=mock_tenant_id,
                project_id=pid_uuid,
                type=DeliverableType.code,
                title="verified one",
                status=DeliverableStatus.delivered,
                proof_state=ProofState.verified,
                verifier_type="software_test",
                proof_summary="12 passed",
                verified_at=now,
            ),
            Deliverable(
                tenant_id=mock_tenant_id,
                project_id=pid_uuid,
                type=DeliverableType.doc,
                title="missing proof",
                status=DeliverableStatus.delivered,
                proof_state=ProofState.verification_missing,
            ),
            Deliverable(
                tenant_id=mock_tenant_id,
                project_id=pid_uuid,
                type=DeliverableType.code,
                title="failed",
                status=DeliverableStatus.delivered,
                proof_state=ProofState.verification_failed,
            ),
        ]
    )
    await db_session.commit()

    body = (
        await client.get(
            f"/api/v1/brief?project_id={pid}",
            headers={"Authorization": "Bearer fake"},
        )
    ).json()
    titles = [d["title"] for d in body["shipped"]]
    assert titles == ["verified one"]
    assert body["shipped"][0]["proof_state"] == "verified"
    assert body["shipped"][0]["verifier_type"] == "software_test"
    assert body["shipped"][0]["proof_summary"] == "12 passed"


@pytest.mark.asyncio
async def test_brief_needs_decision_orders_blocking_first(client, db_session, mock_tenant_id) -> None:
    pid = await _make_project(client)
    pid_uuid = uuid.UUID(pid)
    now = datetime.now(timezone.utc)

    db_session.add_all(
        [
            Decision(
                tenant_id=mock_tenant_id,
                project_id=pid_uuid,
                question="non-blocking older",
                options=["yes"],
                blocking=False,
                created_at=now - timedelta(hours=2),
            ),
            Decision(
                tenant_id=mock_tenant_id,
                project_id=pid_uuid,
                question="blocking newer",
                options=["yes", "no"],
                blocking=True,
                created_at=now,
            ),
            # Resolved decision must NOT appear.
            Decision(
                tenant_id=mock_tenant_id,
                project_id=pid_uuid,
                question="already resolved",
                options=["yes"],
                blocking=True,
                resolved_at=now,
                resolution="yes",
            ),
        ]
    )
    await db_session.commit()

    body = (
        await client.get(
            f"/api/v1/brief?project_id={pid}",
            headers={"Authorization": "Bearer fake"},
        )
    ).json()
    questions = [d["question"] for d in body["needs_decision"]]
    assert questions == ["blocking newer", "non-blocking older"]


@pytest.mark.asyncio
async def test_brief_blocked_and_running_split_by_status(client, db_session, mock_tenant_id) -> None:
    pid = await _make_project(client)
    pid_uuid = uuid.UUID(pid)

    request = Request(
        tenant_id=mock_tenant_id,
        project_id=pid_uuid,
        intent_summary="ship the dashboard",
        status=RequestStatus.open,
    )
    db_session.add(request)
    await db_session.flush()

    db_session.add_all(
        [
            ExecutionRun(
                tenant_id=mock_tenant_id,
                project_id=pid_uuid,
                request_id=request.id,
                status=RunStatus.blocked,
                error_message="missing API key",
            ),
            ExecutionRun(
                tenant_id=mock_tenant_id,
                project_id=pid_uuid,
                request_id=request.id,
                status=RunStatus.running,
            ),
            ExecutionRun(
                tenant_id=mock_tenant_id,
                project_id=pid_uuid,
                request_id=request.id,
                status=RunStatus.pending,
            ),
            # Done runs should not appear in either section.
            ExecutionRun(
                tenant_id=mock_tenant_id,
                project_id=pid_uuid,
                request_id=request.id,
                status=RunStatus.done,
            ),
        ]
    )
    await db_session.commit()

    body = (
        await client.get(
            f"/api/v1/brief?project_id={pid}",
            headers={"Authorization": "Bearer fake"},
        )
    ).json()
    blocked = body["blocked"]
    running = body["running"]
    assert len(blocked) == 1
    assert blocked[0]["status"] == "blocked"
    assert blocked[0]["error_message"] == "missing API key"
    assert blocked[0]["request_intent"] == "ship the dashboard"
    statuses = sorted(r["status"] for r in running)
    assert statuses == ["pending", "running"]


# ─── Tenant isolation ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_brief_404_for_foreign_project(client, db_session) -> None:
    other_tid = uuid.uuid4()
    db_session.add(
        Tenant(
            id=other_tid,
            name="Other",
            slug=f"o-{uuid.uuid4().hex[:8]}",
            owner_user_id="x",
        )
    )
    await db_session.commit()
    foreign = Project(tenant_id=other_tid, name="Hidden", description="")
    db_session.add(foreign)
    await db_session.commit()
    await db_session.refresh(foreign)

    resp = await client.get(
        f"/api/v1/brief?project_id={foreign.id}",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_company_brief_does_not_leak_foreign_tenant_rows(
    client, db_session, mock_tenant_id
) -> None:
    """Cross-project Brief is tenant-scoped — a foreign tenant's
    verified deliverable must NOT appear."""
    other_tid = uuid.uuid4()
    db_session.add(
        Tenant(
            id=other_tid,
            name="Foreign",
            slug=f"f-{uuid.uuid4().hex[:8]}",
            owner_user_id="x",
        )
    )
    await db_session.commit()
    foreign_project = Project(tenant_id=other_tid, name="Hidden", description="")
    db_session.add(foreign_project)
    await db_session.commit()
    await db_session.refresh(foreign_project)

    db_session.add(
        Deliverable(
            tenant_id=other_tid,
            project_id=foreign_project.id,
            type=DeliverableType.code,
            title="leak attempt",
            status=DeliverableStatus.delivered,
            proof_state=ProofState.verified,
            verified_at=datetime.now(timezone.utc),
        )
    )
    # And one of mine that should appear.
    pid = await _make_project(client)
    db_session.add(
        Deliverable(
            tenant_id=mock_tenant_id,
            project_id=uuid.UUID(pid),
            type=DeliverableType.doc,
            title="my own",
            status=DeliverableStatus.delivered,
            proof_state=ProofState.verified,
            verified_at=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()

    body = (
        await client.get(
            "/api/v1/brief",
            headers={"Authorization": "Bearer fake"},
        )
    ).json()
    titles = [d["title"] for d in body["shipped"]]
    assert "my own" in titles
    assert "leak attempt" not in titles


@pytest.mark.asyncio
async def test_brief_respects_limit_param(client, db_session, mock_tenant_id) -> None:
    pid = await _make_project(client)
    pid_uuid = uuid.UUID(pid)
    now = datetime.now(timezone.utc)

    for i in range(15):
        db_session.add(
            Deliverable(
                tenant_id=mock_tenant_id,
                project_id=pid_uuid,
                type=DeliverableType.code,
                title=f"d-{i}",
                status=DeliverableStatus.delivered,
                proof_state=ProofState.verified,
                verified_at=now - timedelta(minutes=i),
            )
        )
    await db_session.commit()

    body = (
        await client.get(
            f"/api/v1/brief?project_id={pid}&limit=5",
            headers={"Authorization": "Bearer fake"},
        )
    ).json()
    assert len(body["shipped"]) == 5
    # Newest first, so d-0 is at the top.
    assert body["shipped"][0]["title"] == "d-0"
