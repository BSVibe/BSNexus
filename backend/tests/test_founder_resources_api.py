"""API contract tests for Requests / Deliverables / Decisions list endpoints."""

from __future__ import annotations

import uuid

import pytest

from backend.src.models import (
    Decision,
    Deliverable,
    DeliverableStatus,
    DeliverableType,
    Project,
    Request,
)


async def _make_project(client, name: str = "Proj") -> str:
    resp = await client.post(
        "/api/v1/projects",
        json={"name": name},
        headers={"Authorization": "Bearer fake"},
    )
    return resp.json()["id"]


# ─── Requests list ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_requests_empty(client):
    pid = await _make_project(client)
    resp = await client.get(
        f"/api/v1/projects/{pid}/requests",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_requests_returns_created(client, db_session, mock_tenant_id):
    pid = await _make_project(client)
    req = Request(
        tenant_id=mock_tenant_id,
        project_id=uuid.UUID(pid),
        intent_summary="Ship it",
    )
    db_session.add(req)
    await db_session.commit()

    rows = (
        await client.get(
            f"/api/v1/projects/{pid}/requests",
            headers={"Authorization": "Bearer fake"},
        )
    ).json()
    assert len(rows) == 1
    assert rows[0]["intent_summary"] == "Ship it"
    assert rows[0]["status"] == "open"


@pytest.mark.asyncio
async def test_list_requests_filters_by_project(client, db_session, mock_tenant_id):
    pid_a = await _make_project(client, "A")
    pid_b = await _make_project(client, "B")

    db_session.add(
        Request(
            tenant_id=mock_tenant_id,
            project_id=uuid.UUID(pid_a),
            intent_summary="A request",
        )
    )
    db_session.add(
        Request(
            tenant_id=mock_tenant_id,
            project_id=uuid.UUID(pid_b),
            intent_summary="B request",
        )
    )
    await db_session.commit()

    a_rows = (
        await client.get(
            f"/api/v1/projects/{pid_a}/requests",
            headers={"Authorization": "Bearer fake"},
        )
    ).json()
    assert len(a_rows) == 1
    assert a_rows[0]["intent_summary"] == "A request"


# ─── Deliverables list ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_deliverables_empty(client):
    pid = await _make_project(client)
    resp = await client.get(
        f"/api/v1/projects/{pid}/deliverables",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_deliverables_returns_rows(client, db_session, mock_tenant_id):
    pid = await _make_project(client)
    db_session.add(
        Deliverable(
            tenant_id=mock_tenant_id,
            project_id=uuid.UUID(pid),
            type=DeliverableType.doc,
            title="Design note",
            status=DeliverableStatus.draft,
        )
    )
    await db_session.commit()

    rows = (
        await client.get(
            f"/api/v1/projects/{pid}/deliverables",
            headers={"Authorization": "Bearer fake"},
        )
    ).json()
    assert len(rows) == 1
    assert rows[0]["title"] == "Design note"
    assert rows[0]["type"] == "doc"


# ─── Decisions list + resolve ──────────────────────────────────


@pytest.mark.asyncio
async def test_list_decisions_empty(client):
    pid = await _make_project(client)
    resp = await client.get(
        f"/api/v1/projects/{pid}/decisions",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_decisions_orders_blocking_first(client, db_session, mock_tenant_id):
    pid = await _make_project(client)
    non_blocking = Decision(
        tenant_id=mock_tenant_id,
        project_id=uuid.UUID(pid),
        question="Non-blocking",
        options=["yes"],
        blocking=False,
    )
    blocking = Decision(
        tenant_id=mock_tenant_id,
        project_id=uuid.UUID(pid),
        question="Blocking",
        options=["yes", "no"],
        blocking=True,
    )
    db_session.add_all([non_blocking, blocking])
    await db_session.commit()

    rows = (
        await client.get(
            f"/api/v1/projects/{pid}/decisions",
            headers={"Authorization": "Bearer fake"},
        )
    ).json()
    assert len(rows) == 2
    assert rows[0]["question"] == "Blocking"
    assert rows[1]["question"] == "Non-blocking"


@pytest.mark.asyncio
async def test_resolve_decision_marks_resolved(client, db_session, mock_tenant_id):
    pid = await _make_project(client)
    decision = Decision(
        tenant_id=mock_tenant_id,
        project_id=uuid.UUID(pid),
        question="Pick a tool",
        options=["a", "b"],
        blocking=True,
    )
    db_session.add(decision)
    await db_session.commit()
    await db_session.refresh(decision)

    resp = await client.post(
        f"/api/v1/decisions/{decision.id}/resolve",
        json={"resolution": "a", "resolved_by": "founder"},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["resolution"] == "a"
    assert body["resolved_by"] == "founder"
    assert body["resolved_at"] is not None


@pytest.mark.asyncio
async def test_resolve_decision_404_for_foreign_tenant(client, db_session):
    other_tid = uuid.uuid4()
    from backend.src.models import Tenant

    db_session.add(
        Tenant(
            id=other_tid,
            name="O",
            slug=f"o-{uuid.uuid4().hex[:8]}",
            owner_user_id="x",
        )
    )
    await db_session.commit()
    db_session.add(Project(tenant_id=other_tid, name="Hidden", description=""))
    await db_session.commit()
    foreign_project = (
        await db_session.execute(__import__("sqlalchemy").select(Project).where(Project.tenant_id == other_tid))
    ).scalar_one()

    decision = Decision(
        tenant_id=other_tid,
        project_id=foreign_project.id,
        question="secret",
        options=[],
        blocking=True,
    )
    db_session.add(decision)
    await db_session.commit()
    await db_session.refresh(decision)

    resp = await client.post(
        f"/api/v1/decisions/{decision.id}/resolve",
        json={"resolution": "x"},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 404
