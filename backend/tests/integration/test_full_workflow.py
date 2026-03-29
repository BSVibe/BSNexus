"""Integration tests for full end-to-end workflows."""

from __future__ import annotations

import uuid as uuid_mod
from unittest.mock import AsyncMock, MagicMock

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.main import app
from backend.src.models import Phase, PhaseStatus


# -- Helpers -------------------------------------------------------------------


def _async_iter(items: list):
    """Helper to create an async iterator from a list."""

    async def _gen():
        for item in items:
            yield item

    return _gen()


async def _create_project(client: AsyncClient) -> dict:
    """Create a project via API and return the response data."""
    response = await client.post(
        "/api/v1/projects",
        json={
            "name": "Integration Test Project",
            "description": "Project for integration testing",
            "repo_path": "/test/integration",
        },
    )
    assert response.status_code == 201
    return response.json()


async def _create_phase(client: AsyncClient, project_id: str) -> dict:
    """Create a phase via API and return the response data."""
    response = await client.post(
        f"/api/v1/projects/{project_id}/phases",
        json={
            "name": "Integration Phase",
            "description": "Phase for integration testing",
            "order": 1,
        },
    )
    assert response.status_code == 201
    return response.json()


async def _activate_phase(db_session: AsyncSession, phase_id: str) -> None:
    """Set a phase to active status directly in the database."""
    await db_session.execute(update(Phase).where(Phase.id == uuid_mod.UUID(phase_id)).values(status=PhaseStatus.active))
    await db_session.flush()


async def _create_task(
    client: AsyncClient,
    project_id: str,
    phase_id: str,
    title: str,
    depends_on: list[str] | None = None,
    priority: str = "medium",
) -> dict:
    """Create a task via API and return the response data."""
    response = await client.post(
        "/api/v1/tasks/",
        json={
            "project_id": project_id,
            "phase_id": phase_id,
            "title": title,
            "description": f"Description for {title}",
            "priority": priority,
            "depends_on": depends_on or [],
            "worker_prompt": f"work on {title}",
            "qa_prompt": f"check {title}",
        },
    )
    assert response.status_code == 201
    return response.json()


async def _transition_task(client: AsyncClient, task_id: str, new_status: str, actor: str = "test") -> dict:
    """Transition a task to a new status and return the response data."""
    response = await client.post(
        f"/api/v1/tasks/{task_id}/transition",
        json={"new_status": new_status, "actor": actor},
    )
    assert response.status_code == 200
    return response.json()


# -- Tests ---------------------------------------------------------------------


async def test_project_lifecycle(client: AsyncClient, db_session: AsyncSession):
    """Full lifecycle: project -> phase -> task -> transitions through done."""
    # 1. Create project
    project = await _create_project(client)
    assert project["name"] == "Integration Test Project"
    assert project["status"] == "design"

    # 2. Verify project appears in list
    list_resp = await client.get("/api/v1/projects")
    assert list_resp.status_code == 200
    project_ids = {p["id"] for p in list_resp.json()}
    assert project["id"] in project_ids

    # 3. Get project by ID
    get_resp = await client.get(f"/api/v1/projects/{project['id']}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == project["id"]

    # 4. Create phase
    phase = await _create_phase(client, project["id"])
    assert phase["project_id"] == project["id"]
    assert phase["status"] == "pending"

    # 5. Activate phase (required for tasks to start as ready)
    await _activate_phase(db_session, phase["id"])

    # 6. Create task (no deps + active phase -> starts as ready)
    task = await _create_task(client, project["id"], phase["id"], "Lifecycle Task")
    assert task["status"] == "ready"
    assert task["version"] == 1

    # 7. Transition through full lifecycle: ready -> in_progress -> review -> done
    transition = await _transition_task(client, task["id"], "in_progress")
    assert transition["status"] == "in_progress"
    assert transition["previous_status"] == "ready"

    transition = await _transition_task(client, task["id"], "review")
    assert transition["status"] == "review"
    assert transition["previous_status"] == "in_progress"

    transition = await _transition_task(client, task["id"], "done")
    assert transition["status"] == "done"
    assert transition["previous_status"] == "review"

    # 8. Verify final task state
    final_resp = await client.get(f"/api/v1/tasks/{task['id']}")
    assert final_resp.status_code == 200
    final_task = final_resp.json()
    assert final_task["status"] == "done"
    assert final_task["version"] == 4  # initial 1 + 3 transitions


async def test_dependency_chain(client: AsyncClient, db_session: AsyncSession):
    """Create tasks A, B (depends on A), C (depends on B). Verify dependency promotion."""
    project = await _create_project(client)
    phase = await _create_phase(client, project["id"])
    await _activate_phase(db_session, phase["id"])

    # Create Task A (no deps + active phase -> ready)
    task_a = await _create_task(client, project["id"], phase["id"], "Task A")
    assert task_a["status"] == "ready"

    # Create Task B (depends on A -> waiting)
    task_b = await _create_task(client, project["id"], phase["id"], "Task B", depends_on=[task_a["id"]])
    assert task_b["status"] == "waiting"

    # Create Task C (depends on B -> waiting)
    task_c = await _create_task(client, project["id"], phase["id"], "Task C", depends_on=[task_b["id"]])
    assert task_c["status"] == "waiting"

    # Complete Task A: ready -> in_progress -> review -> done
    await _transition_task(client, task_a["id"], "in_progress")
    await _transition_task(client, task_a["id"], "review")
    await _transition_task(client, task_a["id"], "done")

    # After A is done, B should be promoted to ready
    task_b_resp = await client.get(f"/api/v1/tasks/{task_b['id']}")
    assert task_b_resp.status_code == 200
    assert task_b_resp.json()["status"] == "ready"

    # C should still be waiting (B not done yet)
    task_c_resp = await client.get(f"/api/v1/tasks/{task_c['id']}")
    assert task_c_resp.status_code == 200
    assert task_c_resp.json()["status"] == "waiting"

    # Complete Task B
    await _transition_task(client, task_b["id"], "in_progress")
    await _transition_task(client, task_b["id"], "review")
    await _transition_task(client, task_b["id"], "done")

    # After B is done, C should be promoted to ready
    task_c_resp = await client.get(f"/api/v1/tasks/{task_c['id']}")
    assert task_c_resp.status_code == 200
    assert task_c_resp.json()["status"] == "ready"


async def test_board_snapshot(client: AsyncClient, db_session: AsyncSession, mock_stream_manager):
    """Create project with tasks in different states, verify board endpoint returns correct structure."""
    # Board endpoint requires app.state.redis, so we need to set a mock
    mock_redis = AsyncMock()
    mock_redis.scan_iter = MagicMock(return_value=_async_iter([]))
    mock_redis.hgetall = AsyncMock(return_value={})
    app.state.redis = mock_redis

    project = await _create_project(client)
    phase = await _create_phase(client, project["id"])
    await _activate_phase(db_session, phase["id"])

    # Create task in ready state (no deps + active phase)
    task_ready = await _create_task(client, project["id"], phase["id"], "Ready Task")
    assert task_ready["status"] == "ready"

    # Create a task and move to in_progress
    task_active = await _create_task(client, project["id"], phase["id"], "Active Task")
    await _transition_task(client, task_active["id"], "in_progress")

    # Create a task with dependency (waiting)
    task_waiting = await _create_task(client, project["id"], phase["id"], "Waiting Task", depends_on=[task_ready["id"]])
    assert task_waiting["status"] == "waiting"

    # Get board state
    board_resp = await client.get(f"/api/v1/board/{project['id']}")
    assert board_resp.status_code == 200
    board = board_resp.json()

    # Verify structure
    assert board["project_id"] == project["id"]
    assert "columns" in board
    assert "stats" in board

    # Verify columns contain tasks in correct states (BoardColumn format)
    columns = board["columns"]
    assert len(columns["ready"]["tasks"]) == 1
    assert columns["ready"]["tasks"][0]["title"] == "Ready Task"

    assert len(columns["in_progress"]["tasks"]) == 1
    assert columns["in_progress"]["tasks"][0]["title"] == "Active Task"

    assert len(columns["waiting"]["tasks"]) == 1
    assert columns["waiting"]["tasks"][0]["title"] == "Waiting Task"

    # Verify stats
    stats = board["stats"]
    assert stats["total"] == 3
    assert stats["ready"] == 1
    assert stats["in_progress"] == 1
    assert stats["waiting"] == 1
