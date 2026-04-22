"""Tests for MCP API endpoints."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src import models

pytestmark = pytest.mark.asyncio


# ── Helpers ──────────────────────────────────────────────────────────


async def _create_project_and_phase(
    db: AsyncSession,
    *,
    phase_status: models.PhaseStatus = models.PhaseStatus.active,
) -> tuple[models.Project, models.Phase]:
    """Create a project with one phase for testing."""
    project = models.Project(
        name="Test Project",
        description="desc",
        repo_path="/tmp/test",
    )
    db.add(project)
    await db.flush()

    phase = models.Phase(
        project_id=project.id,
        name="Phase 1",
        description="first",
        branch_name="phase/phase-1",
        order=1,
        status=phase_status,
    )
    db.add(phase)
    await db.flush()
    await db.commit()
    return project, phase


async def _create_task(
    db: AsyncSession,
    project: models.Project,
    phase: models.Phase,
    *,
    status: models.TaskStatus = models.TaskStatus.pending,
    title: str = "Test Task",
) -> models.Task:
    task = models.Task(
        project_id=project.id,
        phase_id=phase.id,
        title=title,
        description="task desc",
        status=status,
        priority=models.TaskPriority.medium,
        task_type=models.TaskType.feature,
        source=models.TaskSource.manual,
        worker_prompt={"prompt": "do stuff"},
        qa_prompt={"prompt": "verify stuff"},
        version=1,
    )
    db.add(task)
    await db.flush()
    await db.commit()
    return task


# ── list_projects ────────────────────────────────────────────────────


async def test_list_projects_empty(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/mcp/projects")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_list_projects_returns_data(client: AsyncClient, db_session: AsyncSession) -> None:
    await _create_project_and_phase(db_session)
    resp = await client.get("/api/v1/mcp/projects")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "Test Project"


# ── get_board_state ──────────────────────────────────────────────────


# ── create_task ──────────────────────────────────────────────────────


async def test_create_task_success(client: AsyncClient, db_session: AsyncSession) -> None:
    project, phase = await _create_project_and_phase(db_session)
    resp = await client.post(
        "/api/v1/mcp/tasks",
        json={
            "project_id": str(project.id),
            "title": "New MCP Task",
            "description": "Created via MCP",
            "task_type": "feature",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "New MCP Task"
    assert data["status"] == "pending"


async def test_create_task_pending_phase(client: AsyncClient, db_session: AsyncSession) -> None:
    project, phase = await _create_project_and_phase(db_session, phase_status=models.PhaseStatus.pending)
    resp = await client.post(
        "/api/v1/mcp/tasks",
        json={
            "project_id": str(project.id),
            "title": "Waiting Task",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "pending"


async def test_create_task_project_not_found(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/mcp/tasks",
        json={
            "project_id": str(uuid.uuid4()),
            "title": "Ghost Task",
        },
    )
    assert resp.status_code == 404


async def test_create_task_no_phase(client: AsyncClient, db_session: AsyncSession) -> None:
    project = models.Project(name="No Phase Project", description="d", repo_path="/tmp/x")
    db_session.add(project)
    await db_session.flush()
    await db_session.commit()

    resp = await client.post(
        "/api/v1/mcp/tasks",
        json={"project_id": str(project.id), "title": "T"},
    )
    assert resp.status_code == 400
    assert "no available phase" in resp.json()["detail"].lower()


# ── update_task_status ───────────────────────────────────────────────


async def test_update_task_status_success(client: AsyncClient, db_session: AsyncSession) -> None:
    project, phase = await _create_project_and_phase(db_session)
    task = await _create_task(db_session, project, phase, status=models.TaskStatus.pending)

    resp = await client.patch(
        f"/api/v1/mcp/tasks/{task.id}/status",
        json={"status": "running"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"


async def test_update_task_status_invalid_transition(client: AsyncClient, db_session: AsyncSession) -> None:
    project, phase = await _create_project_and_phase(db_session)
    task = await _create_task(db_session, project, phase, status=models.TaskStatus.done)

    resp = await client.patch(
        f"/api/v1/mcp/tasks/{task.id}/status",
        json={"status": "running"},
    )
    assert resp.status_code == 400


async def test_update_task_status_not_found(client: AsyncClient) -> None:
    resp = await client.patch(
        f"/api/v1/mcp/tasks/{uuid.uuid4()}/status",
        json={"status": "running"},
    )
    assert resp.status_code == 404


# ── get_task_dependencies ────────────────────────────────────────────


async def test_get_dependencies_no_deps(client: AsyncClient, db_session: AsyncSession) -> None:
    project, phase = await _create_project_and_phase(db_session)
    task = await _create_task(db_session, project, phase)

    resp = await client.get(f"/api/v1/mcp/tasks/{task.id}/dependencies")
    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == str(task.id)
    assert len(data["nodes"]) == 1  # only the root node


async def test_get_dependencies_with_deps(client: AsyncClient, db_session: AsyncSession) -> None:
    project, phase = await _create_project_and_phase(db_session)
    dep_task = await _create_task(db_session, project, phase, title="Dependency")
    main_task = await _create_task(db_session, project, phase, title="Main")

    # Wire dependency
    from backend.src.repositories.task_repository import TaskRepository

    repo = TaskRepository(db_session)
    await repo.add_dependencies(main_task.id, [dep_task.id])
    await db_session.commit()

    resp = await client.get(f"/api/v1/mcp/tasks/{main_task.id}/dependencies")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["nodes"]) == 2


async def test_get_dependencies_not_found(client: AsyncClient) -> None:
    resp = await client.get(f"/api/v1/mcp/tasks/{uuid.uuid4()}/dependencies")
    assert resp.status_code == 404


# ── trigger_executor ─────────────────────────────────────────────────


async def test_trigger_executor_unblock_blocked_task(client: AsyncClient, db_session: AsyncSession) -> None:
    """blocked -> pending: the unblock action."""
    project, phase = await _create_project_and_phase(db_session)
    task = await _create_task(db_session, project, phase, status=models.TaskStatus.blocked)

    resp = await client.post(f"/api/v1/mcp/tasks/{task.id}/execute")
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"


async def test_trigger_executor_rejects_pending(client: AsyncClient, db_session: AsyncSession) -> None:
    """Only blocked tasks can be unblocked via this endpoint."""
    project, phase = await _create_project_and_phase(db_session)
    task = await _create_task(db_session, project, phase, status=models.TaskStatus.pending)

    resp = await client.post(f"/api/v1/mcp/tasks/{task.id}/execute")
    assert resp.status_code == 400


async def test_trigger_executor_rejects_done(client: AsyncClient, db_session: AsyncSession) -> None:
    project, phase = await _create_project_and_phase(db_session)
    task = await _create_task(db_session, project, phase, status=models.TaskStatus.done)

    resp = await client.post(f"/api/v1/mcp/tasks/{task.id}/execute")
    assert resp.status_code == 400


async def test_trigger_executor_not_found(client: AsyncClient) -> None:
    resp = await client.post(f"/api/v1/mcp/tasks/{uuid.uuid4()}/execute")
    assert resp.status_code == 404
