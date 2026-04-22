"""Projects API contract tests."""

from __future__ import annotations

import uuid

import pytest


@pytest.mark.asyncio
async def test_list_projects_empty(client):
    resp = await client.get("/api/v1/projects", headers={"Authorization": "Bearer fake"})
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_create_project_returns_201_and_scopes_to_tenant(client, mock_tenant_id):
    resp = await client.post(
        "/api/v1/projects",
        json={"name": "Demo", "description": "Test run"},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["name"] == "Demo"
    assert data["description"] == "Test run"
    assert data["status"] == "design"
    assert data["tenant_id"] == str(mock_tenant_id)
    assert uuid.UUID(data["id"])


@pytest.mark.asyncio
async def test_create_project_rejects_empty_name(client):
    resp = await client.post(
        "/api/v1/projects",
        json={"name": ""},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_list_projects_shows_only_own_tenant(client, db_session, mock_tenant_id):
    # Create one via the API (tenant-scoped).
    resp = await client.post(
        "/api/v1/projects",
        json={"name": "Mine"},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 201

    # Insert a project under a different tenant directly.
    from backend.src.models import Project, Tenant

    other_tenant = Tenant(
        id=uuid.uuid4(),
        name="Other",
        slug=f"o-{uuid.uuid4().hex[:8]}",
        owner_user_id="other-user",
    )
    db_session.add(other_tenant)
    await db_session.commit()
    db_session.add(
        Project(tenant_id=other_tenant.id, name="Not Mine", description="")
    )
    await db_session.commit()

    resp = await client.get(
        "/api/v1/projects", headers={"Authorization": "Bearer fake"}
    )
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["name"] == "Mine"
    assert rows[0]["tenant_id"] == str(mock_tenant_id)


@pytest.mark.asyncio
async def test_get_project_by_id(client):
    created = (
        await client.post(
            "/api/v1/projects",
            json={"name": "A"},
            headers={"Authorization": "Bearer fake"},
        )
    ).json()

    resp = await client.get(
        f"/api/v1/projects/{created['id']}",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]


@pytest.mark.asyncio
async def test_get_project_404_for_other_tenant(client, db_session):
    from backend.src.models import Project, Tenant

    other_tenant = Tenant(
        id=uuid.uuid4(),
        name="O",
        slug=f"o-{uuid.uuid4().hex[:8]}",
        owner_user_id="other",
    )
    db_session.add(other_tenant)
    await db_session.commit()
    p = Project(tenant_id=other_tenant.id, name="Hidden", description="")
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)

    resp = await client.get(
        f"/api/v1/projects/{p.id}", headers={"Authorization": "Bearer fake"}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_patch_project(client):
    created = (
        await client.post(
            "/api/v1/projects",
            json={"name": "Orig"},
            headers={"Authorization": "Bearer fake"},
        )
    ).json()

    resp = await client.patch(
        f"/api/v1/projects/{created['id']}",
        json={"name": "Renamed", "bsage_workspace_id": "ws-1"},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Renamed"
    assert data["bsage_workspace_id"] == "ws-1"


@pytest.mark.asyncio
async def test_delete_project(client):
    created = (
        await client.post(
            "/api/v1/projects",
            json={"name": "Temp"},
            headers={"Authorization": "Bearer fake"},
        )
    ).json()

    resp = await client.delete(
        f"/api/v1/projects/{created['id']}",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 204

    resp = await client.get(
        f"/api/v1/projects/{created['id']}",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 404
