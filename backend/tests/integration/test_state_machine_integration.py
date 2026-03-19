"""Integration tests for state machine transitions via the API."""
from __future__ import annotations

import uuid as uuid_mod

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.models import Phase, PhaseStatus


# -- Helpers -------------------------------------------------------------------


async def _create_project_and_phase(client: AsyncClient, db_session: AsyncSession) -> tuple[str, str]:
    """Create a project and an active phase, return (project_id, phase_id)."""
    project_resp = await client.post(
        "/api/v1/projects",
        json={
            "name": "State Machine Test Project",
            "description": "Testing state machine",
            "repo_path": "/test/sm",
        },
    )
    assert project_resp.status_code == 201
    project_id = project_resp.json()["id"]

    phase_resp = await client.post(
        f"/api/v1/projects/{project_id}/phases",
        json={
            "name": "SM Phase",
            "description": "State machine phase",
            "order": 1,
        },
    )
    assert phase_resp.status_code == 201
    phase_id = phase_resp.json()["id"]

    # Activate the phase so tasks can start as ready
    await db_session.execute(
        update(Phase).where(Phase.id == uuid_mod.UUID(phase_id)).values(status=PhaseStatus.active)
    )
    await db_session.flush()

    return project_id, phase_id


async def _create_task(
    client: AsyncClient,
    project_id: str,
    phase_id: str,
    title: str,
    depends_on: list[str] | None = None,
) -> dict:
    """Create a task and return its response data."""
    response = await client.post(
        "/api/v1/tasks/",
        json={
            "project_id": project_id,
            "phase_id": phase_id,
            "title": title,
            "description": f"Description for {title}",
            "priority": "medium",
            "depends_on": depends_on or [],
            "worker_prompt": "work",
            "qa_prompt": "check",
        },
    )
    assert response.status_code == 201
    return response.json()


# -- Tests ---------------------------------------------------------------------


async def test_invalid_transition_rejected(client: AsyncClient, db_session: AsyncSession):
    """Try invalid transitions and verify 400 response."""
    project_id, phase_id = await _create_project_and_phase(client, db_session)

    # Create a task that starts as waiting (has a dependency)
    dep_task = await _create_task(client, project_id, phase_id, "Dep Task")
    waiting_task = await _create_task(client, project_id, phase_id, "Waiting Task", depends_on=[dep_task["id"]])
    assert waiting_task["status"] == "waiting"

    # Try invalid transition: waiting -> in_progress (should be waiting -> ready)
    response = await client.post(
        f"/api/v1/tasks/{waiting_task['id']}/transition",
        json={"new_status": "in_progress", "actor": "test"},
    )
    assert response.status_code == 400
    assert "Invalid transition" in response.json()["detail"]

    # Create a ready task and try ready -> done (invalid, must go through in_progress first)
    ready_task = await _create_task(client, project_id, phase_id, "Ready Task")
    assert ready_task["status"] == "ready"

    response = await client.post(
        f"/api/v1/tasks/{ready_task['id']}/transition",
        json={"new_status": "done", "actor": "test"},
    )
    assert response.status_code == 400
    assert "Invalid transition" in response.json()["detail"]


async def test_optimistic_locking_conflict(client: AsyncClient, db_session: AsyncSession):
    """Create task, transition with wrong expected_version, verify 409 response."""
    project_id, phase_id = await _create_project_and_phase(client, db_session)
    task = await _create_task(client, project_id, phase_id, "Locking Task")
    assert task["version"] == 1

    # Try transition with wrong version
    response = await client.post(
        f"/api/v1/tasks/{task['id']}/transition",
        json={"new_status": "queued", "actor": "test", "expected_version": 999},
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "Version conflict" in detail
    assert "999" in detail
    assert str(task["version"]) in detail

    # Verify correct version works
    response = await client.post(
        f"/api/v1/tasks/{task['id']}/transition",
        json={"new_status": "queued", "actor": "test", "expected_version": task["version"]},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "queued"

    # Verify update with wrong version also returns 409
    # First create a waiting task (updatable status)
    dep_task = await _create_task(client, project_id, phase_id, "Dep For Update")
    updatable_task = await _create_task(
        client, project_id, phase_id, "Updatable Task", depends_on=[dep_task["id"]]
    )
    assert updatable_task["status"] == "waiting"

    response = await client.patch(
        f"/api/v1/tasks/{updatable_task['id']}",
        json={"title": "New Title", "expected_version": 999},
    )
    assert response.status_code == 409
    assert "Version conflict" in response.json()["detail"]


async def test_execution_failure_auto_retry_via_api(client: AsyncClient, db_session: AsyncSession):
    """Test the in_progress -> ready transition for execution failure auto-retry."""
    project_id, phase_id = await _create_project_and_phase(client, db_session)
    task = await _create_task(client, project_id, phase_id, "Retry Task")
    assert task["status"] == "ready"

    # Move to: ready -> queued -> in_progress
    await client.post(
        f"/api/v1/tasks/{task['id']}/transition",
        json={"new_status": "queued", "actor": "test"},
    )
    await client.post(
        f"/api/v1/tasks/{task['id']}/transition",
        json={"new_status": "in_progress", "actor": "test"},
    )

    # Execution failure: in_progress -> ready (auto-retry path)
    retry_resp = await client.post(
        f"/api/v1/tasks/{task['id']}/transition",
        json={"new_status": "ready", "actor": "pm", "reason": "Execution failed, retrying"},
    )
    assert retry_resp.status_code == 200
    assert retry_resp.json()["status"] == "ready"

    # Verify task is back to ready state and execution fields are reset
    task_resp = await client.get(f"/api/v1/tasks/{task['id']}")
    assert task_resp.status_code == 200
    final_task = task_resp.json()
    assert final_task["status"] == "ready"
    assert final_task["error_message"] is None


async def test_done_is_terminal(client: AsyncClient, db_session: AsyncSession):
    """Verify that done is a terminal state."""
    project_id, phase_id = await _create_project_and_phase(client, db_session)
    task = await _create_task(client, project_id, phase_id, "Terminal Task")

    # Move through: ready -> queued -> in_progress -> review -> done
    await client.post(f"/api/v1/tasks/{task['id']}/transition", json={"new_status": "queued", "actor": "test"})
    await client.post(f"/api/v1/tasks/{task['id']}/transition", json={"new_status": "in_progress", "actor": "test"})
    await client.post(f"/api/v1/tasks/{task['id']}/transition", json={"new_status": "review", "actor": "test"})
    await client.post(f"/api/v1/tasks/{task['id']}/transition", json={"new_status": "done", "actor": "test"})

    # Try to transition done -> anything (should fail)
    response = await client.post(
        f"/api/v1/tasks/{task['id']}/transition",
        json={"new_status": "ready", "actor": "test"},
    )
    assert response.status_code == 400
    assert "Invalid transition" in response.json()["detail"]
