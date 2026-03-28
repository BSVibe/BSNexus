from __future__ import annotations

import uuid
from datetime import datetime, timezone

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import pytest

from backend.src.models import Phase, PhaseStatus, Project, ProjectStatus

pytestmark = pytest.mark.asyncio

# -- ORM Helpers ---------------------------------------------------------------


async def _create_project(db_session: AsyncSession, **overrides: object) -> Project:
    now = datetime.now(timezone.utc)
    defaults: dict = dict(
        id=uuid.uuid4(),
        name="Test Project",
        description="A test project",
        repo_path="/tmp/repo",
        status=ProjectStatus.active,
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    project = Project(**defaults)
    db_session.add(project)
    await db_session.commit()
    return project


async def _create_phase(db_session: AsyncSession, project_id: uuid.UUID, **overrides: object) -> Phase:
    now = datetime.now(timezone.utc)
    defaults: dict = dict(
        id=uuid.uuid4(),
        project_id=project_id,
        name="Phase Alpha",
        description="First phase",
        branch_name="phase/phase-alpha",
        order=1,
        status=PhaseStatus.pending,
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    phase = Phase(**defaults)
    db_session.add(phase)
    await db_session.commit()
    return phase


# ── POST /api/v1/projects ────────────────────────────────────────────────────


async def test_create_project_success(client: AsyncClient) -> None:
    payload = {"name": "New Proj", "description": "Desc", "repo_path": "/tmp/new"}
    resp = await client.post("/api/v1/projects", json=payload)

    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "New Proj"
    assert body["description"] == "Desc"
    assert body["repo_path"] == "/tmp/new"
    assert body["status"] == "design"
    assert "id" in body
    assert "created_at" in body
    assert body["phases"] == []


async def test_create_project_missing_fields(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/projects", json={"name": "only name"})
    assert resp.status_code == 422


async def test_create_project_empty_body(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/projects", json={})
    assert resp.status_code == 422


# ── GET /api/v1/projects ─────────────────────────────────────────────────────


async def test_list_projects_empty(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/projects")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_list_projects_returns_all(client: AsyncClient, db_session: AsyncSession) -> None:
    await _create_project(db_session, name="P1")
    await _create_project(db_session, name="P2")

    resp = await client.get("/api/v1/projects")
    assert resp.status_code == 200
    names = {p["name"] for p in resp.json()}
    assert names == {"P1", "P2"}


async def test_list_projects_pagination_limit(client: AsyncClient, db_session: AsyncSession) -> None:
    for i in range(5):
        await _create_project(db_session, name=f"P{i}")

    resp = await client.get("/api/v1/projects", params={"limit": 2})
    assert resp.status_code == 200
    assert len(resp.json()) == 2


async def test_list_projects_pagination_offset(client: AsyncClient, db_session: AsyncSession) -> None:
    for i in range(5):
        await _create_project(db_session, name=f"P{i}")

    all_resp = await client.get("/api/v1/projects", params={"limit": 200})
    offset_resp = await client.get("/api/v1/projects", params={"limit": 200, "offset": 3})
    assert len(all_resp.json()) == 5
    assert len(offset_resp.json()) == 2


async def test_list_projects_invalid_offset(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/projects", params={"offset": -1})
    assert resp.status_code == 422


# ── GET /api/v1/projects/{project_id} ────────────────────────────────────────


async def test_get_project_success(client: AsyncClient, db_session: AsyncSession) -> None:
    project = await _create_project(db_session, name="Get Me")
    resp = await client.get(f"/api/v1/projects/{project.id}")

    assert resp.status_code == 200
    assert resp.json()["name"] == "Get Me"
    assert resp.json()["id"] == str(project.id)


async def test_get_project_not_found(client: AsyncClient) -> None:
    fake_id = uuid.uuid4()
    resp = await client.get(f"/api/v1/projects/{fake_id}")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Project not found"


async def test_get_project_invalid_uuid(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/projects/not-a-uuid")
    assert resp.status_code == 422


async def test_get_project_includes_phases_sorted(client: AsyncClient, db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    await _create_phase(db_session, project.id, name="Beta", order=2, branch_name="phase/beta")
    await _create_phase(db_session, project.id, name="Alpha", order=1, branch_name="phase/alpha")

    resp = await client.get(f"/api/v1/projects/{project.id}")
    assert resp.status_code == 200
    phases = resp.json()["phases"]
    assert len(phases) == 2
    assert phases[0]["order"] < phases[1]["order"]


# ── PATCH /api/v1/projects/{project_id} ──────────────────────────────────────


async def test_update_project_name(client: AsyncClient, db_session: AsyncSession) -> None:
    project = await _create_project(db_session, name="Old Name")
    resp = await client.patch(f"/api/v1/projects/{project.id}", json={"name": "New Name"})

    assert resp.status_code == 200
    assert resp.json()["name"] == "New Name"


async def test_update_project_description(client: AsyncClient, db_session: AsyncSession) -> None:
    project = await _create_project(db_session, description="Old")
    resp = await client.patch(f"/api/v1/projects/{project.id}", json={"description": "Updated"})

    assert resp.status_code == 200
    assert resp.json()["description"] == "Updated"


async def test_update_project_status(client: AsyncClient, db_session: AsyncSession) -> None:
    project = await _create_project(db_session, status=ProjectStatus.active)
    resp = await client.patch(f"/api/v1/projects/{project.id}", json={"status": "completed"})

    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"


async def test_update_project_invalid_status(client: AsyncClient, db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    resp = await client.patch(f"/api/v1/projects/{project.id}", json={"status": "nonexistent"})
    assert resp.status_code == 422


async def test_update_project_not_found(client: AsyncClient) -> None:
    fake_id = uuid.uuid4()
    resp = await client.patch(f"/api/v1/projects/{fake_id}", json={"name": "Nope"})
    assert resp.status_code == 404


async def test_update_project_no_fields(client: AsyncClient, db_session: AsyncSession) -> None:
    project = await _create_project(db_session, name="Same")
    resp = await client.patch(f"/api/v1/projects/{project.id}", json={})

    assert resp.status_code == 200
    assert resp.json()["name"] == "Same"


# ── DELETE /api/v1/projects/{project_id} ─────────────────────────────────────


async def test_delete_project_success(client: AsyncClient, db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    resp = await client.delete(f"/api/v1/projects/{project.id}")

    assert resp.status_code == 200
    assert resp.json()["detail"] == "Project deleted"

    # Confirm gone
    get_resp = await client.get(f"/api/v1/projects/{project.id}")
    assert get_resp.status_code == 404


async def test_delete_project_not_found(client: AsyncClient) -> None:
    fake_id = uuid.uuid4()
    resp = await client.delete(f"/api/v1/projects/{fake_id}")
    assert resp.status_code == 404


async def test_delete_project_cascades_phases(client: AsyncClient, db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    await _create_phase(db_session, project.id, name="To Delete")

    resp = await client.delete(f"/api/v1/projects/{project.id}")
    assert resp.status_code == 200

    # Phases should also be gone — listing for deleted project returns 404
    phases_resp = await client.get(f"/api/v1/projects/{project.id}/phases")
    assert phases_resp.status_code == 404


# ── POST /api/v1/projects/batch-delete ───────────────────────────────────────


async def test_batch_delete_multiple(client: AsyncClient, db_session: AsyncSession) -> None:
    p1 = await _create_project(db_session, name="BD1")
    p2 = await _create_project(db_session, name="BD2")
    await _create_project(db_session, name="BD3")

    resp = await client.post("/api/v1/projects/batch-delete", json={"ids": [str(p1.id), str(p2.id)]})
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 2

    # Only BD3 should remain
    list_resp = await client.get("/api/v1/projects")
    assert len(list_resp.json()) == 1
    assert list_resp.json()[0]["name"] == "BD3"


async def test_batch_delete_empty_list(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/projects/batch-delete", json={"ids": []})
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 0


async def test_batch_delete_nonexistent_ids(client: AsyncClient) -> None:
    fake_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    resp = await client.post("/api/v1/projects/batch-delete", json={"ids": fake_ids})
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 0


async def test_batch_delete_mix_existing_and_nonexistent(client: AsyncClient, db_session: AsyncSession) -> None:
    p1 = await _create_project(db_session, name="Real")
    fake_id = uuid.uuid4()

    resp = await client.post("/api/v1/projects/batch-delete", json={"ids": [str(p1.id), str(fake_id)]})
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 1


# ── POST /api/v1/projects/{project_id}/phases ────────────────────────────────


async def test_create_phase_success(client: AsyncClient, db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    payload = {"name": "User Auth", "description": "Auth module", "order": 999}

    resp = await client.post(f"/api/v1/projects/{project.id}/phases", json=payload)
    assert resp.status_code == 201

    body = resp.json()
    assert body["name"] == "User Auth"
    assert body["description"] == "Auth module"
    assert body["project_id"] == str(project.id)
    assert body["status"] == "pending"


async def test_create_phase_branch_name_slugified(client: AsyncClient, db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    payload = {"name": "User Auth & Setup!", "description": "d", "order": 0}

    resp = await client.post(f"/api/v1/projects/{project.id}/phases", json=payload)
    assert resp.status_code == 201
    assert resp.json()["branch_name"] == "phase/user-auth-setup"


async def test_create_phase_auto_order_first(client: AsyncClient, db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    payload = {"name": "First", "description": "d", "order": 999}

    resp = await client.post(f"/api/v1/projects/{project.id}/phases", json=payload)
    assert resp.status_code == 201
    assert resp.json()["order"] == 1  # auto-calculated, ignores input


async def test_create_phase_auto_order_increments(client: AsyncClient, db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    await _create_phase(db_session, project.id, order=3)

    payload = {"name": "Next", "description": "d", "order": 0}
    resp = await client.post(f"/api/v1/projects/{project.id}/phases", json=payload)
    assert resp.status_code == 201
    assert resp.json()["order"] == 4  # max(3) + 1


async def test_create_phase_project_not_found(client: AsyncClient) -> None:
    fake_id = uuid.uuid4()
    payload = {"name": "Orphan", "description": "d", "order": 1}

    resp = await client.post(f"/api/v1/projects/{fake_id}/phases", json=payload)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Project not found"


async def test_create_phase_missing_fields(client: AsyncClient, db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    resp = await client.post(f"/api/v1/projects/{project.id}/phases", json={"name": "only name"})
    assert resp.status_code == 422


# ── GET /api/v1/projects/{project_id}/phases ─────────────────────────────────


async def test_list_phases_empty(client: AsyncClient, db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    resp = await client.get(f"/api/v1/projects/{project.id}/phases")

    assert resp.status_code == 200
    assert resp.json() == []


async def test_list_phases_returns_ordered(client: AsyncClient, db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    await _create_phase(db_session, project.id, name="Second", order=2, branch_name="phase/second")
    await _create_phase(db_session, project.id, name="First", order=1, branch_name="phase/first")

    resp = await client.get(f"/api/v1/projects/{project.id}/phases")
    assert resp.status_code == 200

    phases = resp.json()
    assert len(phases) == 2
    assert phases[0]["name"] == "First"
    assert phases[1]["name"] == "Second"


async def test_list_phases_project_not_found(client: AsyncClient) -> None:
    fake_id = uuid.uuid4()
    resp = await client.get(f"/api/v1/projects/{fake_id}/phases")
    assert resp.status_code == 404


# ── PATCH /api/v1/projects/phases/{phase_id} ─────────────────────────────────


async def test_update_phase_name_and_branch(client: AsyncClient, db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    phase = await _create_phase(db_session, project.id, name="Old Name", branch_name="phase/old-name")

    resp = await client.patch(f"/api/v1/projects/phases/{phase.id}", json={"name": "New Name"})
    assert resp.status_code == 200

    body = resp.json()
    assert body["name"] == "New Name"
    assert body["branch_name"] == "phase/new-name"


async def test_update_phase_description(client: AsyncClient, db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    phase = await _create_phase(db_session, project.id)

    resp = await client.patch(f"/api/v1/projects/phases/{phase.id}", json={"description": "Updated desc"})
    assert resp.status_code == 200
    assert resp.json()["description"] == "Updated desc"


async def test_update_phase_status(client: AsyncClient, db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    phase = await _create_phase(db_session, project.id, status=PhaseStatus.pending)

    resp = await client.patch(f"/api/v1/projects/phases/{phase.id}", json={"status": "active"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"


async def test_update_phase_invalid_status(client: AsyncClient, db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    phase = await _create_phase(db_session, project.id)

    resp = await client.patch(f"/api/v1/projects/phases/{phase.id}", json={"status": "invalid"})
    assert resp.status_code == 422


async def test_update_phase_not_found(client: AsyncClient) -> None:
    fake_id = uuid.uuid4()
    resp = await client.patch(f"/api/v1/projects/phases/{fake_id}", json={"name": "Nope"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Phase not found"


async def test_update_phase_no_fields(client: AsyncClient, db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    phase = await _create_phase(db_session, project.id, name="Unchanged")

    resp = await client.patch(f"/api/v1/projects/phases/{phase.id}", json={})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Unchanged"
    assert resp.json()["branch_name"] == "phase/phase-alpha"


async def test_update_phase_name_slugifies_special_chars(client: AsyncClient, db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    phase = await _create_phase(db_session, project.id)

    resp = await client.patch(f"/api/v1/projects/phases/{phase.id}", json={"name": "API & Integration Tests!"})
    assert resp.status_code == 200
    assert resp.json()["branch_name"] == "phase/api-integration-tests"


# ── Direct-call tests for coverage ──────────────────────────────────────────
# httpx ASGITransport doesn't always register with coverage.py, so we
# call endpoint functions directly to ensure line-level coverage.

<<<<<<< HEAD
import pytest
from backend.src import schemas
from backend.src.api.projects import (
=======
from backend.src import schemas  # noqa: E402
from backend.src.api.projects import (  # noqa: E402
>>>>>>> fix(tests): clean up lint issues across test files
    batch_delete_projects,
    create_phase,
    create_project,
    delete_project,
    get_project,
    list_phases,
    list_projects,
    update_phase,
    update_project,
    PhaseUpdate,
)


async def test_direct_create_project(db_session: AsyncSession) -> None:
    data = schemas.ProjectCreate(name="Direct", description="Desc", repo_path="/r")
    result = await create_project(data, db=db_session)
    assert result.name == "Direct"
    assert result.status == schemas.ProjectStatus.design
    assert result.phases == []


async def test_direct_list_projects(db_session: AsyncSession) -> None:
    await _create_project(db_session, name="A")
    await _create_project(db_session, name="B")
    result = await list_projects(limit=50, offset=0, db=db_session)
    assert len(result) == 2


async def test_direct_list_projects_pagination(db_session: AsyncSession) -> None:
    for i in range(5):
        await _create_project(db_session, name=f"P{i}")
    result = await list_projects(limit=2, offset=1, db=db_session)
    assert len(result) == 2


async def test_direct_get_project_success(db_session: AsyncSession) -> None:
    p = await _create_project(db_session, name="Found")
    result = await get_project(p.id, db=db_session)
    assert result.name == "Found"


async def test_direct_get_project_not_found(db_session: AsyncSession) -> None:
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await get_project(uuid.uuid4(), db=db_session)
    assert exc_info.value.status_code == 404


async def test_direct_get_project_phases_sorted(db_session: AsyncSession) -> None:
    p = await _create_project(db_session)
    await _create_phase(db_session, p.id, name="B", order=2, branch_name="phase/b")
    await _create_phase(db_session, p.id, name="A", order=1, branch_name="phase/a")
    result = await get_project(p.id, db=db_session)
    assert result.phases[0].order < result.phases[1].order


async def test_direct_update_project(db_session: AsyncSession) -> None:
    p = await _create_project(db_session, name="Old")
    data = schemas.ProjectUpdate(name="New", status=schemas.ProjectStatus.completed)
    result = await update_project(p.id, data, db=db_session)
    assert result.name == "New"
    assert result.status == schemas.ProjectStatus.completed


async def test_direct_update_project_not_found(db_session: AsyncSession) -> None:
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await update_project(uuid.uuid4(), schemas.ProjectUpdate(name="X"), db=db_session)
    assert exc_info.value.status_code == 404


async def test_direct_delete_project(db_session: AsyncSession) -> None:
    p = await _create_project(db_session)
    result = await delete_project(p.id, db=db_session)
    assert result.detail == "Project deleted"


async def test_direct_delete_project_not_found(db_session: AsyncSession) -> None:
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await delete_project(uuid.uuid4(), db=db_session)
    assert exc_info.value.status_code == 404


async def test_direct_batch_delete(db_session: AsyncSession) -> None:
    p1 = await _create_project(db_session, name="D1")
    p2 = await _create_project(db_session, name="D2")
    body = schemas.BatchDeleteRequest(ids=[p1.id, p2.id])
    result = await batch_delete_projects(body, db=db_session)
    assert result.deleted == 2


async def test_direct_create_phase(db_session: AsyncSession) -> None:
    p = await _create_project(db_session)
    data = schemas.PhaseCreate(name="Auth Module", description="Auth", order=99)
    result = await create_phase(p.id, data, db=db_session)
    assert result.name == "Auth Module"
    assert result.branch_name == "phase/auth-module"
    assert result.order == 1  # auto-calculated


async def test_direct_create_phase_project_not_found(db_session: AsyncSession) -> None:
    from fastapi import HTTPException
    data = schemas.PhaseCreate(name="X", description="X", order=1)
    with pytest.raises(HTTPException) as exc_info:
        await create_phase(uuid.uuid4(), data, db=db_session)
    assert exc_info.value.status_code == 404


async def test_direct_list_phases(db_session: AsyncSession) -> None:
    p = await _create_project(db_session)
    await _create_phase(db_session, p.id, name="P1", order=1, branch_name="phase/p1")
    result = await list_phases(p.id, db=db_session)
    assert len(result) == 1


async def test_direct_list_phases_project_not_found(db_session: AsyncSession) -> None:
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await list_phases(uuid.uuid4(), db=db_session)
    assert exc_info.value.status_code == 404


async def test_direct_update_phase(db_session: AsyncSession) -> None:
    p = await _create_project(db_session)
    ph = await _create_phase(db_session, p.id, name="Old", branch_name="phase/old")
    data = PhaseUpdate(name="New", status=schemas.PhaseStatus.active)
    result = await update_phase(ph.id, data, db=db_session)
    assert result.name == "New"
    assert result.branch_name == "phase/new"
    assert result.status == schemas.PhaseStatus.active


async def test_direct_update_phase_not_found(db_session: AsyncSession) -> None:
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await update_phase(uuid.uuid4(), PhaseUpdate(name="X"), db=db_session)
    assert exc_info.value.status_code == 404
