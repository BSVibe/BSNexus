from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from httpx import AsyncClient

from backend.src import models, schemas
from backend.src.api.tasks import (
    build_task_response,
    create_task,
    get_task,
    list_project_tasks,
    transition_task,
    update_task,
)
from fastapi import HTTPException

from backend.src.models import Phase, PhaseStatus, Project, ProjectStatus, Task, TaskPriority, TaskStatus

pytestmark = pytest.mark.asyncio


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


# ---------------------------------------------------------------------------
# Direct-call unit tests for coverage (ASGI transport doesn't trace handler bodies)
# ---------------------------------------------------------------------------

def _make_task_orm(**overrides) -> Task:
    """Build a minimal Task ORM object with sensible defaults."""
    now = datetime.now(timezone.utc)
    defaults = dict(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        phase_id=uuid.uuid4(),
        title="T",
        description="d",
        status=TaskStatus.ready,
        priority=TaskPriority.medium,
        task_type=models.TaskType.feature,
        source=models.TaskSource.architect,
        parent_task_id=None,
        worker_prompt={"prompt": "w"},
        qa_prompt={"prompt": "q"},
        branch_name=None,
        commit_hash=None,
        qa_result=None,
        output_path=None,
        error_message=None,
        retry_count=0,
        max_retries=3,
        qa_feedback_history=None,
        version=1,
        created_at=now,
        updated_at=now,
        started_at=None,
        completed_at=None,
        depends_on=[],
    )
    defaults.update(overrides)
    task = MagicMock(spec=Task)
    for k, v in defaults.items():
        setattr(task, k, v)
    return task


# -- build_task_response -------------------------------------------------------

async def test_build_task_response_basic(db_session):
    """build_task_response converts ORM Task to TaskResponse."""
    task = _make_task_orm()
    resp = build_task_response(task)
    assert resp.title == "T"
    assert resp.status == schemas.TaskStatus.ready
    assert resp.depends_on == []


async def test_build_task_response_with_deps(db_session):
    """build_task_response extracts dependency IDs."""
    dep_id = uuid.uuid4()
    dep_mock = MagicMock()
    dep_mock.id = dep_id
    task = _make_task_orm(depends_on=[dep_mock])
    resp = build_task_response(task)
    assert resp.depends_on == [dep_id]


# -- create_task (direct call) ------------------------------------------------

async def test_create_task_active_phase_direct(db_session):
    """create_task sets status=ready when phase is active and no deps."""
    project, phase = await create_project_and_phase(db_session)

    task_data = schemas.TaskCreate(
        project_id=project.id,
        phase_id=phase.id,
        title="Direct Create",
        description="test",
        priority=schemas.TaskPriority.medium,
        worker_prompt="work",
        qa_prompt="check",
        depends_on=[],
    )
    request = MagicMock()
    result = await create_task(task_data, request, _auth=MagicMock(), db=db_session)
    assert result.status == schemas.TaskStatus.ready
    assert result.title == "Direct Create"


async def test_create_task_inactive_phase_direct(db_session):
    """create_task sets status=waiting when phase is pending and no deps."""
    now = datetime.now(timezone.utc)
    project = Project(
        id=uuid.uuid4(), name="P", description="d", repo_path="/r",
        status=ProjectStatus.active, created_at=now, updated_at=now,
    )
    db_session.add(project)
    phase = Phase(
        id=uuid.uuid4(), project_id=project.id, name="Ph", branch_name="b",
        order=1, status=PhaseStatus.pending, created_at=now, updated_at=now,
    )
    db_session.add(phase)
    await db_session.commit()

    task_data = schemas.TaskCreate(
        project_id=project.id, phase_id=phase.id,
        title="Inactive", description="test",
        priority=schemas.TaskPriority.high,
        worker_prompt="w", qa_prompt="q", depends_on=[],
    )
    request = MagicMock()
    result = await create_task(task_data, request, _auth=MagicMock(), db=db_session)
    assert result.status == schemas.TaskStatus.waiting


async def test_create_task_with_deps_direct(db_session):
    """create_task sets status=waiting when depends_on is provided."""
    project, phase = await create_project_and_phase(db_session)

    # Create a dependency task first
    dep_data = schemas.TaskCreate(
        project_id=project.id, phase_id=phase.id,
        title="Dep", description="d",
        priority=schemas.TaskPriority.medium,
        worker_prompt="w", qa_prompt="q", depends_on=[],
    )
    request = MagicMock()
    dep_result = await create_task(dep_data, request, _auth=MagicMock(), db=db_session)

    task_data = schemas.TaskCreate(
        project_id=project.id, phase_id=phase.id,
        title="Dependent", description="d",
        priority=schemas.TaskPriority.medium,
        worker_prompt="w", qa_prompt="q",
        depends_on=[dep_result.id],
    )
    result = await create_task(task_data, request, _auth=MagicMock(), db=db_session)
    assert result.status == schemas.TaskStatus.waiting
    assert dep_result.id in result.depends_on


async def test_create_task_missing_dep_raises_400(db_session):
    """create_task raises 400 when a dependency ID does not exist."""
    project, phase = await create_project_and_phase(db_session)
    fake_id = uuid.uuid4()
    task_data = schemas.TaskCreate(
        project_id=project.id, phase_id=phase.id,
        title="Bad Dep", description="d",
        priority=schemas.TaskPriority.medium,
        worker_prompt="w", qa_prompt="q",
        depends_on=[fake_id],
    )
    request = MagicMock()
    with pytest.raises(HTTPException) as exc_info:
        await create_task(task_data, request, _auth=MagicMock(), db=db_session)
    assert exc_info.value.status_code == 400
    assert "Dependency tasks not found" in exc_info.value.detail


# -- get_task (direct call) ----------------------------------------------------

async def test_get_task_found_direct(db_session):
    """get_task returns TaskResponse for existing task."""
    project, phase = await create_project_and_phase(db_session)
    now = datetime.now(timezone.utc)
    task = Task(
        id=uuid.uuid4(), project_id=project.id, phase_id=phase.id,
        title="Find Me", status=TaskStatus.ready,
        priority=TaskPriority.medium, version=1,
        created_at=now, updated_at=now,
    )
    db_session.add(task)
    await db_session.commit()

    result = await get_task(task.id, include_history=False, db=db_session)
    assert result.id == task.id
    assert result.title == "Find Me"


async def test_get_task_not_found_direct(db_session):
    """get_task raises 404 when task does not exist."""
    with pytest.raises(HTTPException) as exc_info:
        await get_task(uuid.uuid4(), include_history=False, db=db_session)
    assert exc_info.value.status_code == 404


# -- update_task (direct call) -------------------------------------------------

async def test_update_task_ready_direct(db_session):
    """update_task succeeds for task in ready status."""
    project, phase = await create_project_and_phase(db_session)
    now = datetime.now(timezone.utc)
    task = Task(
        id=uuid.uuid4(), project_id=project.id, phase_id=phase.id,
        title="Updatable", status=TaskStatus.ready,
        priority=TaskPriority.medium, version=1,
        created_at=now, updated_at=now,
    )
    db_session.add(task)
    await db_session.commit()

    update_data = schemas.TaskUpdate(title="Updated Title")
    result = await update_task(task.id, update_data, db=db_session)
    assert result.title == "Updated Title"
    assert result.version == 2


async def test_update_task_waiting_direct(db_session):
    """update_task succeeds for task in waiting status."""
    project, phase = await create_project_and_phase(db_session)
    now = datetime.now(timezone.utc)
    task = Task(
        id=uuid.uuid4(), project_id=project.id, phase_id=phase.id,
        title="Waiting", status=TaskStatus.waiting,
        priority=TaskPriority.low, version=1,
        created_at=now, updated_at=now,
    )
    db_session.add(task)
    await db_session.commit()

    update_data = schemas.TaskUpdate(description="new desc", priority=schemas.TaskPriority.critical)
    result = await update_task(task.id, update_data, db=db_session)
    assert result.description == "new desc"
    assert result.priority == schemas.TaskPriority.critical


async def test_update_task_not_found_direct(db_session):
    """update_task raises 404 when task does not exist."""
    from fastapi import HTTPException
    update_data = schemas.TaskUpdate(title="Nope")
    with pytest.raises(HTTPException) as exc_info:
        await update_task(uuid.uuid4(), update_data, db=db_session)
    assert exc_info.value.status_code == 404


async def test_update_task_non_editable_status_direct(db_session):
    """update_task raises 400 for task in review status."""
    project, phase = await create_project_and_phase(db_session)
    now = datetime.now(timezone.utc)
    task = Task(
        id=uuid.uuid4(), project_id=project.id, phase_id=phase.id,
        title="Review", status=TaskStatus.review,
        priority=TaskPriority.high, version=1,
        created_at=now, updated_at=now,
    )
    db_session.add(task)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await update_task(task.id, schemas.TaskUpdate(title="No"), db=db_session)
    assert exc_info.value.status_code == 400
    assert "waiting or ready" in exc_info.value.detail


async def test_update_task_done_status_direct(db_session):
    """update_task raises 400 for task in done status."""
    project, phase = await create_project_and_phase(db_session)
    now = datetime.now(timezone.utc)
    task = Task(
        id=uuid.uuid4(), project_id=project.id, phase_id=phase.id,
        title="Done", status=TaskStatus.done,
        priority=TaskPriority.low, version=2,
        created_at=now, updated_at=now,
    )
    db_session.add(task)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await update_task(task.id, schemas.TaskUpdate(title="No"), db=db_session)
    assert exc_info.value.status_code == 400


async def test_update_task_version_conflict_direct(db_session):
    """update_task raises 409 on version mismatch."""
    project, phase = await create_project_and_phase(db_session)
    now = datetime.now(timezone.utc)
    task = Task(
        id=uuid.uuid4(), project_id=project.id, phase_id=phase.id,
        title="Versioned", status=TaskStatus.ready,
        priority=TaskPriority.medium, version=1,
        created_at=now, updated_at=now,
    )
    db_session.add(task)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await update_task(task.id, schemas.TaskUpdate(title="X", expected_version=999), db=db_session)
    assert exc_info.value.status_code == 409


# -- transition_task (direct call) ---------------------------------------------

async def test_transition_task_not_found_direct(db_session):
    """transition_task raises 404 for non-existent task."""
    from fastapi import HTTPException
    request = MagicMock()
    request.app.state = MagicMock()
    request.app.state.stream_manager = None
    transition = schemas.TaskTransition(new_status=schemas.TaskStatus.in_progress, actor="test")
    with pytest.raises(HTTPException) as exc_info:
        await transition_task(uuid.uuid4(), transition, request, db=db_session)
    assert exc_info.value.status_code == 404


async def test_transition_task_valid_direct(db_session, mock_stream_manager):
    """transition_task succeeds for valid READY -> IN_PROGRESS."""
    project, phase = await create_project_and_phase(db_session)
    now = datetime.now(timezone.utc)
    task = Task(
        id=uuid.uuid4(), project_id=project.id, phase_id=phase.id,
        title="Trans", status=TaskStatus.ready,
        priority=TaskPriority.medium, version=1,
        created_at=now, updated_at=now,
    )
    db_session.add(task)
    await db_session.commit()

    request = MagicMock()
    request.app.state.stream_manager = mock_stream_manager
    transition = schemas.TaskTransition(new_status=schemas.TaskStatus.in_progress, actor="worker")
    result = await transition_task(task.id, transition, request, db=db_session)
    assert result.status == schemas.TaskStatus.in_progress
    assert result.previous_status == schemas.TaskStatus.ready


async def test_transition_task_invalid_direct(db_session, mock_stream_manager):
    """transition_task raises 400 for invalid READY -> DONE."""
    project, phase = await create_project_and_phase(db_session)
    now = datetime.now(timezone.utc)
    task = Task(
        id=uuid.uuid4(), project_id=project.id, phase_id=phase.id,
        title="BadTrans", status=TaskStatus.ready,
        priority=TaskPriority.medium, version=1,
        created_at=now, updated_at=now,
    )
    db_session.add(task)
    await db_session.commit()

    request = MagicMock()
    request.app.state.stream_manager = mock_stream_manager
    from fastapi import HTTPException
    transition = schemas.TaskTransition(new_status=schemas.TaskStatus.done, actor="test")
    with pytest.raises(HTTPException) as exc_info:
        await transition_task(task.id, transition, request, db=db_session)
    assert exc_info.value.status_code == 400


async def test_transition_task_version_conflict_direct(db_session, mock_stream_manager):
    """transition_task raises 409 on version mismatch."""
    project, phase = await create_project_and_phase(db_session)
    now = datetime.now(timezone.utc)
    task = Task(
        id=uuid.uuid4(), project_id=project.id, phase_id=phase.id,
        title="VerTrans", status=TaskStatus.ready,
        priority=TaskPriority.medium, version=1,
        created_at=now, updated_at=now,
    )
    db_session.add(task)
    await db_session.commit()

    request = MagicMock()
    request.app.state.stream_manager = mock_stream_manager
    from fastapi import HTTPException
    transition = schemas.TaskTransition(
        new_status=schemas.TaskStatus.in_progress, actor="test", expected_version=999,
    )
    with pytest.raises(HTTPException) as exc_info:
        await transition_task(task.id, transition, request, db=db_session)
    assert exc_info.value.status_code == 409


# -- list_project_tasks (direct call) ------------------------------------------

async def test_list_project_tasks_direct(db_session):
    """list_project_tasks returns tasks for a project."""
    project, phase = await create_project_and_phase(db_session)
    now = datetime.now(timezone.utc)
    for title in ["A", "B"]:
        db_session.add(Task(
            id=uuid.uuid4(), project_id=project.id, phase_id=phase.id,
            title=title, status=TaskStatus.ready,
            priority=TaskPriority.medium, version=1,
            created_at=now, updated_at=now,
        ))
    await db_session.commit()

    result = await list_project_tasks(
        project.id, status=None, phase_id=None, priority=None, limit=50, offset=0, db=db_session,
    )
    assert len(result) == 2


async def test_list_project_tasks_with_filters_direct(db_session):
    """list_project_tasks filters by status and priority."""
    project, phase = await create_project_and_phase(db_session)
    now = datetime.now(timezone.utc)
    db_session.add(Task(
        id=uuid.uuid4(), project_id=project.id, phase_id=phase.id,
        title="High Ready", status=TaskStatus.ready,
        priority=TaskPriority.high, version=1,
        created_at=now, updated_at=now,
    ))
    db_session.add(Task(
        id=uuid.uuid4(), project_id=project.id, phase_id=phase.id,
        title="Low Done", status=TaskStatus.done,
        priority=TaskPriority.low, version=1,
        created_at=now, updated_at=now,
    ))
    await db_session.commit()

    result = await list_project_tasks(
        project.id, status="ready", phase_id=None, priority="high", limit=50, offset=0, db=db_session,
    )
    assert len(result) == 1
    assert result[0].priority == schemas.TaskPriority.high


async def test_list_project_tasks_pagination_direct(db_session):
    """list_project_tasks respects limit and offset."""
    project, phase = await create_project_and_phase(db_session)
    now = datetime.now(timezone.utc)
    for i in range(5):
        db_session.add(Task(
            id=uuid.uuid4(), project_id=project.id, phase_id=phase.id,
            title=f"Task {i}", status=TaskStatus.ready,
            priority=TaskPriority.medium, version=1,
            created_at=now, updated_at=now,
        ))
    await db_session.commit()

    result = await list_project_tasks(
        project.id, status=None, phase_id=None, priority=None, limit=2, offset=0, db=db_session,
    )
    assert len(result) == 2

    result2 = await list_project_tasks(
        project.id, status=None, phase_id=None, priority=None, limit=10, offset=3, db=db_session,
    )
    assert len(result2) == 2


async def test_list_project_tasks_invalid_status_direct(db_session):
    """list_project_tasks raises 400 for invalid status string."""
    project, _ = await create_project_and_phase(db_session)
    with pytest.raises(HTTPException) as exc_info:
        await list_project_tasks(
            project.id, status="bogus", phase_id=None, priority=None, limit=50, offset=0, db=db_session,
        )
    assert exc_info.value.status_code == 400


async def test_list_project_tasks_phase_filter_direct(db_session):
    """list_project_tasks filters by phase_id."""
    project, phase = await create_project_and_phase(db_session)
    now = datetime.now(timezone.utc)
    phase2 = Phase(
        id=uuid.uuid4(), project_id=project.id, name="Ph2", branch_name="b2",
        order=2, status=PhaseStatus.active, created_at=now, updated_at=now,
    )
    db_session.add(phase2)
    db_session.add(Task(
        id=uuid.uuid4(), project_id=project.id, phase_id=phase.id,
        title="Ph1 Task", status=TaskStatus.ready,
        priority=TaskPriority.medium, version=1,
        created_at=now, updated_at=now,
    ))
    db_session.add(Task(
        id=uuid.uuid4(), project_id=project.id, phase_id=phase2.id,
        title="Ph2 Task", status=TaskStatus.ready,
        priority=TaskPriority.medium, version=1,
        created_at=now, updated_at=now,
    ))
    await db_session.commit()

    result = await list_project_tasks(
        project.id, status=None, phase_id=phase2.id, priority=None, limit=50, offset=0, db=db_session,
    )
    assert len(result) == 1
    assert result[0].title == "Ph2 Task"
