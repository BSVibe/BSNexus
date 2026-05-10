from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from backend.src.core.domain import RequestStatus
from backend.src.models import Direction, Project, Request


async def _make_project(db_session, tenant_id: uuid.UUID, name: str = "Greenfield") -> Project:
    project = Project(tenant_id=tenant_id, name=name, description="")
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)
    return project


@pytest.mark.asyncio
async def test_post_direction_with_project_creates_request(client, db_session, mock_tenant_id):
    project = await _make_project(db_session, mock_tenant_id)

    resp = await client.post(
        "/api/v1/directions",
        json={
            "project_id": str(project.id),
            "source": "web",
            "body": "Ship a proof-first dashboard",
            "target_hint": "dashboard",
        },
        headers={"Authorization": "Bearer fake"},
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["direction"]["project_id"] == str(project.id)
    assert body["direction"]["source"] == "web"
    assert body["direction"]["body"] == "Ship a proof-first dashboard"
    assert body["request"]["project_id"] == str(project.id)
    assert body["request"]["origin_direction_id"] == body["direction"]["id"]
    assert body["request"]["intent"] == "Ship a proof-first dashboard"
    assert body["request"]["status"] == "open"
    assert body["routing"] is None
    # G7.1 — backend no longer ships display strings on the wire. The
    # frontend computes ack copy from the response state (request !=
    # null vs routing != null) and renders via i18n. Surfacing the
    # English ``acknowledgement`` field forced English-only ack on a
    # Korean UI.
    assert "acknowledgement" not in body

    direction = await db_session.get(Direction, uuid.UUID(body["direction"]["id"]))
    assert direction is not None
    assert direction.body == "Ship a proof-first dashboard"

    request = await db_session.get(Request, uuid.UUID(body["request"]["id"]))
    assert request is not None
    assert request.origin_direction_id == direction.id


@pytest.mark.asyncio
async def test_post_direction_auto_routes_when_single_project(client, db_session, mock_tenant_id):
    project = await _make_project(db_session, mock_tenant_id)

    resp = await client.post(
        "/api/v1/directions",
        json={"source": "mobile_web", "body": "Make the onboarding brief tighter"},
        headers={"Authorization": "Bearer fake"},
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["direction"]["project_id"] == str(project.id)
    assert body["request"]["project_id"] == str(project.id)
    assert body["routing"] is None


@pytest.mark.asyncio
async def test_post_direction_asks_routing_question_when_project_is_ambiguous(
    client, db_session, mock_tenant_id
):
    alpha = await _make_project(db_session, mock_tenant_id, "Alpha")
    beta = await _make_project(db_session, mock_tenant_id, "Beta")

    resp = await client.post(
        "/api/v1/directions",
        json={"source": "web", "body": "Ship the mobile review flow"},
        headers={"Authorization": "Bearer fake"},
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["direction"]["project_id"] is None
    assert body["request"] is None
    assert body["routing"]["required"] is True
    assert body["routing"]["question"] == "Which project should this direction apply to?"
    assert {option["project_id"] for option in body["routing"]["options"]} == {
        str(alpha.id),
        str(beta.id),
    }

    stored_requests = (await db_session.execute(select(Request))).scalars().all()
    assert stored_requests == []


@pytest.mark.asyncio
async def test_post_direction_uses_unique_target_hint(client, db_session, mock_tenant_id):
    await _make_project(db_session, mock_tenant_id, "Marketing Site")
    app_project = await _make_project(db_session, mock_tenant_id, "Mobile App")

    resp = await client.post(
        "/api/v1/directions",
        json={
            "source": "web",
            "body": "Tighten the daily brief card",
            "target_hint": "mobile",
        },
        headers={"Authorization": "Bearer fake"},
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["direction"]["project_id"] == str(app_project.id)
    assert body["request"]["project_id"] == str(app_project.id)
    assert body["routing"] is None


@pytest.mark.asyncio
async def test_flat_list_endpoints_return_empty_contracts(client, db_session, mock_tenant_id):
    project = await _make_project(db_session, mock_tenant_id)

    for path in (
        f"/api/v1/requests?project_id={project.id}",
        f"/api/v1/decisions?project_id={project.id}",
        f"/api/v1/deliverables?project_id={project.id}",
    ):
        resp = await client.get(path, headers={"Authorization": "Bearer fake"})
        assert resp.status_code == 200, path
        assert resp.json() == []

    brief = await client.get(
        f"/api/v1/brief?project_id={project.id}",
        headers={"Authorization": "Bearer fake"},
    )
    assert brief.status_code == 200
    assert brief.json() == {
        "scope": "project",
        "project_id": str(project.id),
        "sections": {
            "shipped": [],
            "needs_decision": [],
            "blocked": [],
            "running": [],
            "next": [],
        },
        "generated_at": brief.json()["generated_at"],
    }


@pytest.mark.asyncio
async def test_flat_endpoints_are_tenant_scoped(client, db_session, mock_tenant_id):
    own_project = await _make_project(db_session, mock_tenant_id, "Own")
    other_tenant = uuid.uuid4()
    from backend.src.models import Tenant

    db_session.add(
        Tenant(
            id=other_tenant,
            name="Other",
            slug=f"other-{other_tenant.hex[:8]}",
            owner_user_id="other-user",
        )
    )
    await db_session.commit()
    other_project = await _make_project(db_session, other_tenant, "Other")
    db_session.add(
        Request(
            tenant_id=other_tenant,
            project_id=other_project.id,
            intent="Hidden",
        )
    )
    await db_session.commit()

    own_rows = await client.get(
        f"/api/v1/requests?project_id={own_project.id}",
        headers={"Authorization": "Bearer fake"},
    )
    cross_project_rows = await client.get("/api/v1/requests", headers={"Authorization": "Bearer fake"})

    assert own_rows.status_code == 200
    assert own_rows.json() == []
    assert cross_project_rows.status_code == 200
    assert cross_project_rows.json() == []


def test_request_status_has_proof_blocking_states():
    assert RequestStatus.shipped.value == "shipped"
    assert RequestStatus.review_ready.value == "review_ready"
    assert RequestStatus.blocked.value == "blocked"
