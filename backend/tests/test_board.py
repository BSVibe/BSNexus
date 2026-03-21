from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from backend.src.api.board import _board_event_generator, _build_task_response, _get_board_data
from backend.src.main import app
from backend.src.models import Phase, PhaseStatus, Project, ProjectStatus, Task, TaskPriority, TaskStatus


async def create_project_phase_task(db_session, status: TaskStatus = TaskStatus.ready) -> tuple[Project, Phase, Task]:
    """Helper to create a project with a phase and task."""
    now = datetime.now(timezone.utc)
    project = Project(
        id=uuid.uuid4(),
        name="Test Project",
        description="Test",
        repo_path="/test",
        status=ProjectStatus.active,
        created_at=now,
        updated_at=now,
    )
    db_session.add(project)
    await db_session.flush()

    phase = Phase(
        id=uuid.uuid4(),
        project_id=project.id,
        name="Phase 1",
        branch_name="phase-1",
        order=1,
        status=PhaseStatus.active,
        created_at=now,
        updated_at=now,
    )
    db_session.add(phase)
    await db_session.flush()

    task = Task(
        id=uuid.uuid4(),
        project_id=project.id,
        phase_id=phase.id,
        title="Test Task",
        status=status,
        priority=TaskPriority.medium,
        version=1,
        created_at=now,
        updated_at=now,
    )
    db_session.add(task)
    await db_session.commit()

    return project, phase, task


@pytest.fixture(autouse=True)
def mock_redis():
    """Set app.state.redis to an AsyncMock for all board tests."""
    mock = AsyncMock()
    mock.scan_iter = AsyncMock(return_value=[]).__aiter__
    # Make scan_iter return an empty async iterator by default
    mock.scan_iter.return_value = _empty_async_iter()
    app.state.redis = mock
    yield mock


async def _empty_async_iter():
    """Empty async iterator helper."""
    return
    yield  # noqa: RET504 — makes this an async generator


@pytest.mark.asyncio
async def test_get_board_empty_project(client, db_session, mock_redis):
    """GET /api/board/{project_id} with no tasks returns all columns empty and stats all zeros."""
    now = datetime.now(timezone.utc)
    project = Project(
        id=uuid.uuid4(),
        name="Empty Project",
        description="No tasks",
        repo_path="/empty",
        status=ProjectStatus.active,
        created_at=now,
        updated_at=now,
    )
    db_session.add(project)
    await db_session.commit()

    response = await client.get(f"/api/v1/board/{project.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["project_id"] == str(project.id)

    # All kanban columns should be empty (redesign is not a kanban column)
    kanban_statuses = [s for s in TaskStatus if s != TaskStatus.redesign]
    for status in kanban_statuses:
        assert status.value in data["columns"]
        assert data["columns"][status.value] == {"tasks": []}

    # redesign should NOT be in columns
    assert "redesign" not in data["columns"]

    # redesign_tasks should be empty
    assert data["redesign_tasks"] == []

    # Stats should all be zero
    assert data["stats"]["total"] == 0


@pytest.mark.asyncio
async def test_get_board_with_tasks(client, db_session, mock_redis):
    """GET /api/board/{project_id} groups tasks by status into correct columns."""
    now = datetime.now(timezone.utc)
    project = Project(
        id=uuid.uuid4(),
        name="Board Project",
        description="With tasks",
        repo_path="/board",
        status=ProjectStatus.active,
        created_at=now,
        updated_at=now,
    )
    db_session.add(project)
    await db_session.flush()

    phase = Phase(
        id=uuid.uuid4(),
        project_id=project.id,
        name="Phase 1",
        branch_name="phase-1",
        order=1,
        status=PhaseStatus.active,
        created_at=now,
        updated_at=now,
    )
    db_session.add(phase)
    await db_session.flush()

    # Create tasks in different statuses
    statuses_to_create = [TaskStatus.ready, TaskStatus.ready, TaskStatus.in_progress, TaskStatus.done]
    for i, status in enumerate(statuses_to_create):
        task = Task(
            id=uuid.uuid4(),
            project_id=project.id,
            phase_id=phase.id,
            title=f"Task {i}",
            status=status,
            priority=TaskPriority.medium,
            version=1,
            created_at=now,
            updated_at=now,
        )
        db_session.add(task)

    await db_session.commit()

    response = await client.get(f"/api/v1/board/{project.id}")

    assert response.status_code == 200
    data = response.json()

    # Check task grouping (columns wrapped in BoardColumn format)
    assert len(data["columns"]["ready"]["tasks"]) == 2
    assert len(data["columns"]["in_progress"]["tasks"]) == 1
    assert len(data["columns"]["done"]["tasks"]) == 1
    assert len(data["columns"]["waiting"]["tasks"]) == 0
    assert len(data["columns"]["review"]["tasks"]) == 0
    assert "redesign" not in data["columns"]


@pytest.mark.asyncio
async def test_get_board_stats(client, db_session, mock_redis):
    """GET /api/board/{project_id} returns accurate stat counts."""
    now = datetime.now(timezone.utc)
    project = Project(
        id=uuid.uuid4(),
        name="Stats Project",
        description="For stats",
        repo_path="/stats",
        status=ProjectStatus.active,
        created_at=now,
        updated_at=now,
    )
    db_session.add(project)
    await db_session.flush()

    phase = Phase(
        id=uuid.uuid4(),
        project_id=project.id,
        name="Phase 1",
        branch_name="phase-1",
        order=1,
        status=PhaseStatus.active,
        created_at=now,
        updated_at=now,
    )
    db_session.add(phase)
    await db_session.flush()

    # Create 3 ready tasks and 2 done tasks
    for i in range(3):
        db_session.add(
            Task(
                id=uuid.uuid4(),
                project_id=project.id,
                phase_id=phase.id,
                title=f"Ready Task {i}",
                status=TaskStatus.ready,
                priority=TaskPriority.medium,
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
    for i in range(2):
        db_session.add(
            Task(
                id=uuid.uuid4(),
                project_id=project.id,
                phase_id=phase.id,
                title=f"Done Task {i}",
                status=TaskStatus.done,
                priority=TaskPriority.medium,
                version=1,
                created_at=now,
                updated_at=now,
            )
        )

    await db_session.commit()

    response = await client.get(f"/api/v1/board/{project.id}")

    assert response.status_code == 200
    data = response.json()

    assert data["stats"]["total"] == 5
    assert data["stats"]["ready"] == 3
    assert data["stats"]["done"] == 2
    assert data["stats"]["waiting"] == 0
    assert data["stats"]["in_progress"] == 0


@pytest.mark.asyncio
async def test_get_board_data_direct(db_session):
    """Directly call _get_board_data to cover lines 69-85."""
    project, phase, task = await create_project_phase_task(db_session, status=TaskStatus.ready)
    data = await _get_board_data(str(project.id), db_session)
    assert data["project_id"] == str(project.id)
    assert data["stats"]["total"] == 1
    assert data["stats"]["ready"] == 1
    assert len(data["columns"]["ready"]["tasks"]) == 1
    assert "redesign" not in data["columns"]
    assert str(phase.id) in data["phases"]


@pytest.mark.asyncio
async def test_get_board_data_redesign_task(db_session):
    """Cover the redesign branch in _get_board_data (line 72-73)."""
    project, phase, _task = await create_project_phase_task(db_session, status=TaskStatus.redesign)
    data = await _get_board_data(str(project.id), db_session)
    assert len(data["redesign_tasks"]) == 1
    assert data["stats"]["redesign"] == 1


# -- SSE Board Events ----------------------------------------------------------


@pytest.mark.asyncio
async def test_board_event_generator_yields_matching_events():
    """_board_event_generator should yield events matching the project_id."""
    project_id = str(uuid.uuid4())
    other_project_id = str(uuid.uuid4())

    mock_redis = AsyncMock()

    # Simulate xread returning messages: one matching, one not
    call_count = 0

    async def mock_xread(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return [
                (
                    "events:board",
                    [
                        ("1-0", {"project_id": project_id, "event": "task_transition", "task_id": "t1"}),
                        ("2-0", {"project_id": other_project_id, "event": "task_transition", "task_id": "t2"}),
                    ],
                )
            ]
        # After first call, raise CancelledError to stop the generator
        raise asyncio.CancelledError()

    mock_redis.xread = mock_xread

    events: list[dict] = []
    try:
        async for event in _board_event_generator(project_id, mock_redis):
            events.append(event)
    except asyncio.CancelledError:
        pass

    # Only the matching event should be yielded
    assert len(events) == 1
    assert "event" not in events[0]  # no named SSE event key — sent as unnamed message
    data = json.loads(events[0]["data"])
    assert data["event"] == "task_transition"
    assert data["task_id"] == "t1"
    assert data["project_id"] == project_id


@pytest.mark.asyncio
async def test_board_event_generator_handles_empty_xread():
    """_board_event_generator should handle empty xread responses gracefully."""
    project_id = str(uuid.uuid4())
    mock_redis = AsyncMock()

    call_count = 0

    async def mock_xread(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            return []
        raise asyncio.CancelledError()

    mock_redis.xread = mock_xread

    events: list[dict] = []
    try:
        async for event in _board_event_generator(project_id, mock_redis):
            events.append(event)
    except asyncio.CancelledError:
        pass

    assert len(events) == 0
    assert call_count == 3


@pytest.mark.asyncio
async def test_board_events_endpoint_returns_sse_response(client, db_session, mock_redis):
    """GET /api/v1/board/{project_id}/events should return SSE content type."""
    project, _phase, _task = await create_project_phase_task(db_session)

    # Configure mock_redis.xread to return empty then cancel
    call_count = 0

    async def mock_xread(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return []
        raise asyncio.CancelledError()

    mock_redis.xread = mock_xread

    response = await client.get(f"/api/v1/board/{project.id}/events", headers={"Accept": "text/event-stream"})

    # SSE responses return 200 with text/event-stream content type
    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_build_task_response_includes_all_fields(db_session):
    """_build_task_response should map all Task fields to TaskResponse."""
    project, phase, _task = await create_project_phase_task(db_session, status=TaskStatus.ready)

    now = datetime.now(timezone.utc)
    task = Task(
        id=uuid.uuid4(), project_id=project.id, phase_id=phase.id,
        title="T", status=TaskStatus.ready, priority=TaskPriority.high,
        version=2, retry_count=1, max_retries=5,
        branch_name="feat/x", commit_hash="abc123",
        created_at=now, updated_at=now,
    )
    db_session.add(task)
    await db_session.commit()

    # Reload with eager loading via TaskRepository to avoid lazy-load issues
    from backend.src.repositories.task_repository import TaskRepository
    repo = TaskRepository(db_session)
    loaded_task = await repo.get_by_id(task.id)

    resp = _build_task_response(loaded_task)

    assert resp.id == task.id
    assert resp.title == "T"
    assert resp.status.value == "ready"
    assert resp.priority.value == "high"
    assert resp.version == 2
    assert resp.retry_count == 1
    assert resp.max_retries == 5
    assert resp.branch_name == "feat/x"
    assert resp.commit_hash == "abc123"
    assert resp.depends_on == []


@pytest.mark.asyncio
async def test_board_event_generator_handles_exception():
    """Cover the exception handler in _board_event_generator (lines 136-138)."""
    from unittest.mock import patch

    project_id = str(uuid.uuid4())
    mock_redis = AsyncMock()

    call_count = 0

    async def mock_xread(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ConnectionError("redis down")
        # After retry, cancel to stop the loop
        raise asyncio.CancelledError()

    mock_redis.xread = mock_xread

    async def fast_sleep(seconds: float) -> None:
        pass

    events: list[dict] = []
    with patch("asyncio.sleep", new=fast_sleep):
        try:
            async for event in _board_event_generator(project_id, mock_redis):
                events.append(event)
        except asyncio.CancelledError:
            pass

    assert len(events) == 0
    assert call_count == 2  # First call raised, second cancelled after sleep
