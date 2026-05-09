from __future__ import annotations

import uuid

import pytest

from backend.src.core.domain import RequestStatus
from backend.src.models import Direction, Project, Request


async def _make_project(db_session, tenant_id: uuid.UUID, name: str = "Greenfield") -> Project:
    project = Project(tenant_id=tenant_id, name=name, description="")
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)
    return project


@pytest.mark.asyncio
async def test_post_direction_persists_without_dispatch(client, db_session, mock_tenant_id):
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
    assert body["project_id"] == str(project.id)
    assert body["source"] == "web"
    assert body["body"] == "Ship a proof-first dashboard"

    direction = await db_session.get(Direction, uuid.UUID(body["id"]))
    assert direction is not None
    assert direction.body == "Ship a proof-first dashboard"


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
