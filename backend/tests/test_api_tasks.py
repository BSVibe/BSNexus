from __future__ import annotations

import uuid
from datetime import datetime, timezone

from httpx import AsyncClient

from backend.src.models import Phase, PhaseStatus, Project, ProjectStatus, Task, TaskPriority, TaskStatus


async def create_project_and_phase(db_session) -> tuple[Project, Phase]:
    """Helper to create a project and phase for task tests."""
    now = datetime.now(timezone.utc)
    project = Project(
        id=uuid.uuid4(),
        name="Test Project",
        description="Test Description",
        repo_path="/test/repo",
        status=ProjectStatus.active,
        created_at=now,
        updated_at=now,
    )
    db_session.add(project)

    phase = Phase(
        id=uuid.uuid4(),
        project_id=project.id,
        name="Test Phase",
        branch_name="phase/test",
        order=1,
        status=PhaseStatus.active,
        created_at=now,
        updated_at=now,
    )
    db_session.add(phase)
    await db_session.commit()
    return project, phase


async def test_create_task_success(client: AsyncClient, db_session):
    """POST /api/tasks/ returns 201 with valid data."""
    project, phase = await create_project_and_phase(db_session)

    response = await client.post(
        "/api/v1/tasks/",
        json={
            "project_id": str(project.id),
            "phase_id": str(phase.id),
            "title": "Test Task",
            "description": "Test description",
            "priority": "medium",
            "depends_on": [],
            "worker_prompt": "do something",
            "qa_prompt": "check something",
        },
    )

    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Test Task"
    assert data["status"] == "ready"  # No dependencies means READY
    assert data["priority"] == "medium"
    assert data["project_id"] == str(project.id)
    assert data["phase_id"] == str(phase.id)


async def test_create_task_dependency_not_found_400(client: AsyncClient, db_session):
    """POST /api/tasks/ with non-existent dependency returns 400."""
    project, phase = await create_project_and_phase(db_session)
    fake_dep_id = str(uuid.uuid4())

    response = await client.post(
        "/api/v1/tasks/",
        json={
            "project_id": str(project.id),
            "phase_id": str(phase.id),
            "title": "Task with bad dep",
            "description": "Test",
            "priority": "medium",
            "depends_on": [fake_dep_id],
            "worker_prompt": "do something",
            "qa_prompt": "check something",
        },
    )

    assert response.status_code == 400
    assert "Dependency tasks not found" in response.json()["detail"]


async def test_get_task_success(client: AsyncClient, db_session):
    """GET /api/tasks/{id} returns 200."""
    project, phase = await create_project_and_phase(db_session)

    # Create a task via API
    create_response = await client.post(
        "/api/v1/tasks/",
        json={
            "project_id": str(project.id),
            "phase_id": str(phase.id),
            "title": "Get Me Task",
            "description": "For retrieval",
            "priority": "high",
            "depends_on": [],
            "worker_prompt": "work",
            "qa_prompt": "check",
        },
    )
    task_id = create_response.json()["id"]

    response = await client.get(f"/api/v1/tasks/{task_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == task_id
    assert data["title"] == "Get Me Task"


async def test_get_task_not_found_404(client: AsyncClient, db_session):
    """GET /api/tasks/{random_uuid} returns 404."""
    random_id = str(uuid.uuid4())
    response = await client.get(f"/api/v1/tasks/{random_id}")

    assert response.status_code == 404
    assert response.json()["detail"] == "Task not found"


async def test_update_task_waiting_status(client: AsyncClient, db_session):
    """PATCH /api/tasks/{id} in waiting status returns 200."""
    project, phase = await create_project_and_phase(db_session)

    # Create a task with a dependency so it starts in WAITING status
    # First create the dependency task
    dep_response = await client.post(
        "/api/v1/tasks/",
        json={
            "project_id": str(project.id),
            "phase_id": str(phase.id),
            "title": "Dep Task",
            "description": "dependency",
            "priority": "medium",
            "depends_on": [],
            "worker_prompt": "work",
            "qa_prompt": "check",
        },
    )
    dep_id = dep_response.json()["id"]

    # Create task that depends on the first one (will be WAITING)
    create_response = await client.post(
        "/api/v1/tasks/",
        json={
            "project_id": str(project.id),
            "phase_id": str(phase.id),
            "title": "Waiting Task",
            "description": "will wait",
            "priority": "low",
            "depends_on": [dep_id],
            "worker_prompt": "work",
            "qa_prompt": "check",
        },
    )
    task_id = create_response.json()["id"]
    assert create_response.json()["status"] == "waiting"

    response = await client.patch(
        f"/api/v1/tasks/{task_id}",
        json={"title": "Updated Title", "priority": "critical"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Updated Title"
    assert data["priority"] == "critical"


async def test_update_task_in_progress_400(client: AsyncClient, db_session):
    """PATCH /api/tasks/{id} in IN_PROGRESS status returns 400."""
    project, phase = await create_project_and_phase(db_session)

    # Create task directly in IN_PROGRESS status in the DB
    now = datetime.now(timezone.utc)
    task = Task(
        id=uuid.uuid4(),
        project_id=project.id,
        phase_id=phase.id,
        title="In Progress Task",
        status=TaskStatus.in_progress,
        priority=TaskPriority.medium,
        version=1,
        created_at=now,
        updated_at=now,
    )
    db_session.add(task)
    await db_session.commit()

    response = await client.patch(
        f"/api/v1/tasks/{task.id}",
        json={"title": "Should Fail"},
    )

    assert response.status_code == 400
    assert "waiting or ready" in response.json()["detail"]


async def test_transition_task_valid(client: AsyncClient, db_session):
    """POST /api/tasks/{id}/transition with valid transition succeeds."""
    project, phase = await create_project_and_phase(db_session)

    # Create a task via API (no deps, so it starts as READY)
    create_response = await client.post(
        "/api/v1/tasks/",
        json={
            "project_id": str(project.id),
            "phase_id": str(phase.id),
            "title": "Transition Task",
            "description": "for transition",
            "priority": "medium",
            "depends_on": [],
            "worker_prompt": "work",
            "qa_prompt": "check",
        },
    )
    task_id = create_response.json()["id"]
    assert create_response.json()["status"] == "ready"

    # Transition READY -> IN_PROGRESS
    response = await client.post(
        f"/api/v1/tasks/{task_id}/transition",
        json={"new_status": "in_progress", "actor": "test"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "in_progress"
    assert data["previous_status"] == "ready"


async def test_transition_task_invalid_400(client: AsyncClient, db_session):
    """POST /api/tasks/{id}/transition with invalid transition returns 400."""
    project, phase = await create_project_and_phase(db_session)

    # Create a task (starts as READY)
    create_response = await client.post(
        "/api/v1/tasks/",
        json={
            "project_id": str(project.id),
            "phase_id": str(phase.id),
            "title": "Invalid Transition Task",
            "description": "for invalid transition",
            "priority": "medium",
            "depends_on": [],
            "worker_prompt": "work",
            "qa_prompt": "check",
        },
    )
    task_id = create_response.json()["id"]

    # Try invalid transition READY -> DONE
    response = await client.post(
        f"/api/v1/tasks/{task_id}/transition",
        json={"new_status": "done", "actor": "test"},
    )

    assert response.status_code == 400
    assert "Invalid transition" in response.json()["detail"]


async def test_list_project_tasks(client: AsyncClient, db_session):
    """GET /api/tasks/by-project/{project_id} returns list of tasks."""
    project, phase = await create_project_and_phase(db_session)

    # Create two tasks
    for title in ["Task One", "Task Two"]:
        await client.post(
            "/api/v1/tasks/",
            json={
                "project_id": str(project.id),
                "phase_id": str(phase.id),
                "title": title,
                "description": f"Description for {title}",
                "priority": "medium",
                "depends_on": [],
                "worker_prompt": "work",
                "qa_prompt": "check",
            },
        )

    response = await client.get(f"/api/v1/tasks/by-project/{project.id}")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    titles = {t["title"] for t in data}
    assert "Task One" in titles
    assert "Task Two" in titles


async def test_transition_with_matching_version(client: AsyncClient, db_session):
    """POST /api/tasks/{id}/transition with matching expected_version succeeds."""
    project, phase = await create_project_and_phase(db_session)

    create_response = await client.post(
        "/api/v1/tasks/",
        json={
            "project_id": str(project.id),
            "phase_id": str(phase.id),
            "title": "Version Match Task",
            "description": "test",
            "priority": "medium",
            "depends_on": [],
            "worker_prompt": "work",
            "qa_prompt": "check",
        },
    )
    task_id = create_response.json()["id"]
    version = create_response.json()["version"]

    response = await client.post(
        f"/api/v1/tasks/{task_id}/transition",
        json={"new_status": "in_progress", "actor": "test", "expected_version": version},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "in_progress"


async def test_transition_with_mismatched_version_409(client: AsyncClient, db_session):
    """POST /api/tasks/{id}/transition with wrong expected_version returns 409."""
    project, phase = await create_project_and_phase(db_session)

    create_response = await client.post(
        "/api/v1/tasks/",
        json={
            "project_id": str(project.id),
            "phase_id": str(phase.id),
            "title": "Version Mismatch Task",
            "description": "test",
            "priority": "medium",
            "depends_on": [],
            "worker_prompt": "work",
            "qa_prompt": "check",
        },
    )
    task_id = create_response.json()["id"]

    response = await client.post(
        f"/api/v1/tasks/{task_id}/transition",
        json={"new_status": "in_progress", "actor": "test", "expected_version": 999},
    )

    assert response.status_code == 409
    assert "Version conflict" in response.json()["detail"]
    assert "999" in response.json()["detail"]


async def test_transition_without_expected_version(client: AsyncClient, db_session):
    """POST /api/tasks/{id}/transition without expected_version still works (backward compat)."""
    project, phase = await create_project_and_phase(db_session)

    create_response = await client.post(
        "/api/v1/tasks/",
        json={
            "project_id": str(project.id),
            "phase_id": str(phase.id),
            "title": "No Version Task",
            "description": "test",
            "priority": "medium",
            "depends_on": [],
            "worker_prompt": "work",
            "qa_prompt": "check",
        },
    )
    task_id = create_response.json()["id"]

    response = await client.post(
        f"/api/v1/tasks/{task_id}/transition",
        json={"new_status": "in_progress", "actor": "test"},
    )

    assert response.status_code == 200


async def test_update_with_mismatched_version_409(client: AsyncClient, db_session):
    """PATCH /api/tasks/{id} with wrong expected_version returns 409."""
    project, phase = await create_project_and_phase(db_session)

    # Create a task with dependency so it's in WAITING status (updatable)
    dep_response = await client.post(
        "/api/v1/tasks/",
        json={
            "project_id": str(project.id),
            "phase_id": str(phase.id),
            "title": "Dep Task",
            "description": "dep",
            "priority": "medium",
            "depends_on": [],
            "worker_prompt": "work",
            "qa_prompt": "check",
        },
    )
    dep_id = dep_response.json()["id"]

    create_response = await client.post(
        "/api/v1/tasks/",
        json={
            "project_id": str(project.id),
            "phase_id": str(phase.id),
            "title": "Update Version Task",
            "description": "test",
            "priority": "medium",
            "depends_on": [dep_id],
            "worker_prompt": "work",
            "qa_prompt": "check",
        },
    )
    task_id = create_response.json()["id"]

    response = await client.patch(
        f"/api/v1/tasks/{task_id}",
        json={"title": "New Title", "expected_version": 999},
    )

    assert response.status_code == 409
    assert "Version conflict" in response.json()["detail"]


async def test_list_tasks_invalid_status_returns_400(client: AsyncClient, db_session):
    """GET /api/tasks/by-project/{id}?status=invalid returns 400."""
    project, _ = await create_project_and_phase(db_session)

    response = await client.get(f"/api/v1/tasks/by-project/{project.id}?status=not_a_status")

    assert response.status_code == 400


async def test_list_tasks_invalid_priority_returns_400(client: AsyncClient, db_session):
    """GET /api/tasks/by-project/{id}?priority=invalid returns 400."""
    project, _ = await create_project_and_phase(db_session)

    response = await client.get(f"/api/v1/tasks/by-project/{project.id}?priority=super_urgent")

    assert response.status_code == 400


async def test_list_tasks_with_status_filter(client: AsyncClient, db_session):
    """GET /api/tasks/by-project/{id}?status=ready returns only ready tasks."""
    project, phase = await create_project_and_phase(db_session)
    # Create a ready task via API
    await client.post(
        "/api/v1/tasks/",
        json={
            "project_id": str(project.id),
            "phase_id": str(phase.id),
            "title": "Ready Task",
            "description": "test",
            "priority": "medium",
            "depends_on": [],
            "worker_prompt": "work",
            "qa_prompt": "check",
        },
    )
    # Create a task that starts as DONE directly in DB
    now = datetime.now(timezone.utc)
    done_task = Task(
        id=uuid.uuid4(),
        project_id=project.id,
        phase_id=phase.id,
        title="Done Task",
        status=TaskStatus.done,
        priority=TaskPriority.medium,
        version=1,
        created_at=now,
        updated_at=now,
    )
    db_session.add(done_task)
    await db_session.commit()

    response = await client.get(f"/api/v1/tasks/by-project/{project.id}?status=ready")

    assert response.status_code == 200
    data = response.json()
    assert all(t["status"] == "ready" for t in data)
    assert len(data) == 1


async def test_update_task_increments_version(client: AsyncClient, db_session):
    """PATCH /api/tasks/{id} increments the task version."""
    project, phase = await create_project_and_phase(db_session)

    # Create task with dependency so it starts in WAITING status
    dep_response = await client.post(
        "/api/v1/tasks/",
        json={
            "project_id": str(project.id),
            "phase_id": str(phase.id),
            "title": "Dep Task",
            "description": "dep",
            "priority": "medium",
            "depends_on": [],
            "worker_prompt": "work",
            "qa_prompt": "check",
        },
    )
    dep_id = dep_response.json()["id"]

    create_response = await client.post(
        "/api/v1/tasks/",
        json={
            "project_id": str(project.id),
            "phase_id": str(phase.id),
            "title": "Version Task",
            "description": "test",
            "priority": "medium",
            "depends_on": [dep_id],
            "worker_prompt": "work",
            "qa_prompt": "check",
        },
    )
    task_id = create_response.json()["id"]
    original_version = create_response.json()["version"]

    response = await client.patch(f"/api/v1/tasks/{task_id}", json={"title": "Updated"})

    assert response.status_code == 200
    assert response.json()["version"] == original_version + 1


async def test_get_task_with_include_history(client: AsyncClient, db_session):
    """GET /api/tasks/{id}?include_history=true loads history relationship."""
    project, phase = await create_project_and_phase(db_session)

    create_response = await client.post(
        "/api/v1/tasks/",
        json={
            "project_id": str(project.id),
            "phase_id": str(phase.id),
            "title": "History Task",
            "description": "test",
            "priority": "medium",
            "depends_on": [],
            "worker_prompt": "work",
            "qa_prompt": "check",
        },
    )
    task_id = create_response.json()["id"]

    response = await client.get(f"/api/v1/tasks/{task_id}?include_history=true")

    assert response.status_code == 200
    assert response.json()["id"] == task_id


async def test_409_response_contains_current_version(client: AsyncClient, db_session):
    """409 response detail includes the current version number."""
    project, phase = await create_project_and_phase(db_session)

    create_response = await client.post(
        "/api/v1/tasks/",
        json={
            "project_id": str(project.id),
            "phase_id": str(phase.id),
            "title": "Version Info Task",
            "description": "test",
            "priority": "medium",
            "depends_on": [],
            "worker_prompt": "work",
            "qa_prompt": "check",
        },
    )
    task_id = create_response.json()["id"]
    current_version = create_response.json()["version"]

    response = await client.post(
        f"/api/v1/tasks/{task_id}/transition",
        json={"new_status": "in_progress", "actor": "test", "expected_version": 999},
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert str(current_version) in detail
