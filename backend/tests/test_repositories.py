"""Tests for TaskRepository and PhaseRepository covering uncovered lines."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from backend.src.models import Phase, PhaseStatus, Project, ProjectStatus, Task, TaskPriority, TaskStatus
from backend.src.repositories.phase_repository import PhaseRepository
from backend.src.repositories.task_repository import TaskRepository

pytestmark = pytest.mark.asyncio

# -- Helpers ------------------------------------------------------------------


async def make_project(db_session) -> Project:
    project = Project(
        id=uuid.uuid4(),
        name="Repo Test Project",
        description="Test",
        repo_path="/repo/test",
        status=ProjectStatus.active,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(project)
    await db_session.flush()
    return project


async def make_phase(
    db_session,
    project_id: uuid.UUID,
    order: int = 1,
    status: PhaseStatus = PhaseStatus.active,
) -> Phase:
    phase = Phase(
        id=uuid.uuid4(),
        project_id=project_id,
        name=f"Phase {order}",
        branch_name=f"branch/{order}",
        order=order,
        status=status,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(phase)
    await db_session.flush()
    return phase


async def make_task(
    db_session,
    project_id: uuid.UUID,
    phase_id: uuid.UUID,
    status: TaskStatus = TaskStatus.pending,
    priority: TaskPriority = TaskPriority.medium,
) -> Task:
    task = Task(
        id=uuid.uuid4(),
        project_id=project_id,
        phase_id=phase_id,
        title="Test Task",
        status=status,
        priority=priority,
        version=1,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(task)
    await db_session.flush()
    return task


# -- TaskRepository Tests -----------------------------------------------------


class TestTaskRepository:
    async def test_get_by_id_with_history(self, db_session):
        """get_by_id(load_history=True) loads the history relationship."""
        project = await make_project(db_session)
        phase = await make_phase(db_session, project.id)
        task = await make_task(db_session, project.id, phase.id)
        await db_session.commit()

        repo = TaskRepository(db_session)
        result = await repo.get_by_id(task.id, load_history=True)

        assert result is not None
        assert result.id == task.id
        assert result.history is not None  # relationship is loaded

    async def test_list_by_project_with_status_filter(self, db_session):
        """list_by_project filters by status."""
        project = await make_project(db_session)
        phase = await make_phase(db_session, project.id)
        await make_task(db_session, project.id, phase.id, status=TaskStatus.pending)
        await make_task(db_session, project.id, phase.id, status=TaskStatus.done)
        await db_session.commit()

        repo = TaskRepository(db_session)
        results = await repo.list_by_project(project.id, status=TaskStatus.pending)

        assert len(results) == 1
        assert results[0].status == TaskStatus.pending

    async def test_list_by_project_with_phase_filter(self, db_session):
        """list_by_project filters by phase_id."""
        project = await make_project(db_session)
        phase1 = await make_phase(db_session, project.id, order=1)
        phase2 = await make_phase(db_session, project.id, order=2, status=PhaseStatus.pending)
        await make_task(db_session, project.id, phase1.id)
        await make_task(db_session, project.id, phase2.id)
        await db_session.commit()

        repo = TaskRepository(db_session)
        results = await repo.list_by_project(project.id, phase_id=phase1.id)

        assert len(results) == 1
        assert results[0].phase_id == phase1.id

    async def test_list_by_project_with_priority_filter(self, db_session):
        """list_by_project filters by priority."""
        project = await make_project(db_session)
        phase = await make_phase(db_session, project.id)
        await make_task(db_session, project.id, phase.id, priority=TaskPriority.high)
        await make_task(db_session, project.id, phase.id, priority=TaskPriority.low)
        await db_session.commit()

        repo = TaskRepository(db_session)
        results = await repo.list_by_project(project.id, priority=TaskPriority.high)

        assert len(results) == 1
        assert results[0].priority == TaskPriority.high

    async def test_validate_dependencies_exist_empty_list(self, db_session):
        """validate_dependencies_exist returns [] for empty input."""
        repo = TaskRepository(db_session)
        missing = await repo.validate_dependencies_exist([])
        assert missing == []

    async def test_detect_circular_dependency_self_reference(self, db_session):
        """detect_circular_dependency returns True when task depends on itself."""
        project = await make_project(db_session)
        phase = await make_phase(db_session, project.id)
        task = await make_task(db_session, project.id, phase.id)
        await db_session.commit()

        repo = TaskRepository(db_session)
        is_circular = await repo.detect_circular_dependency(task.id, [task.id])
        assert is_circular is True

    async def test_detect_circular_dependency_no_cycle(self, db_session):
        """detect_circular_dependency returns False when no cycle exists."""
        project = await make_project(db_session)
        phase = await make_phase(db_session, project.id)
        task_a = await make_task(db_session, project.id, phase.id)
        task_b = await make_task(db_session, project.id, phase.id)
        await db_session.commit()

        repo = TaskRepository(db_session)
        is_circular = await repo.detect_circular_dependency(task_b.id, [task_a.id])
        assert is_circular is False

    async def test_detect_circular_dependency_transitive_cycle(self, db_session):
        """detect_circular_dependency returns True for transitive cycle A→B→A."""
        project = await make_project(db_session)
        phase = await make_phase(db_session, project.id)
        task_a = await make_task(db_session, project.id, phase.id)
        task_b = await make_task(db_session, project.id, phase.id)
        await db_session.commit()

        repo = TaskRepository(db_session)
        # A depends on B
        await repo.add_dependencies(task_a.id, [task_b.id])
        await db_session.commit()

        # Trying to make B depend on A would be circular
        is_circular = await repo.detect_circular_dependency(task_b.id, [task_a.id])
        assert is_circular is True

    async def test_get_dependency_ids(self, db_session):
        """get_dependency_ids returns dep IDs for a task."""
        project = await make_project(db_session)
        phase = await make_phase(db_session, project.id)
        task_a = await make_task(db_session, project.id, phase.id)
        task_b = await make_task(db_session, project.id, phase.id)
        await db_session.commit()

        repo = TaskRepository(db_session)
        await repo.add_dependencies(task_a.id, [task_b.id])
        await db_session.commit()

        dep_ids = await repo.get_dependency_ids(task_a.id)
        assert task_b.id in dep_ids

    async def test_get_incomplete_dependency_count(self, db_session):
        """get_incomplete_dependency_count counts non-done deps."""
        project = await make_project(db_session)
        phase = await make_phase(db_session, project.id)
        task_main = await make_task(db_session, project.id, phase.id)
        dep_done = await make_task(db_session, project.id, phase.id, status=TaskStatus.done)
        dep_pending = await make_task(db_session, project.id, phase.id, status=TaskStatus.pending)
        await db_session.commit()

        repo = TaskRepository(db_session)
        await repo.add_dependencies(task_main.id, [dep_done.id, dep_pending.id])
        await db_session.commit()

        count = await repo.get_incomplete_dependency_count(task_main.id)
        assert count == 1

    async def test_get_incomplete_dependency_count_no_deps(self, db_session):
        """get_incomplete_dependency_count returns 0 when no deps exist."""
        project = await make_project(db_session)
        phase = await make_phase(db_session, project.id)
        task = await make_task(db_session, project.id, phase.id)
        await db_session.commit()

        repo = TaskRepository(db_session)
        count = await repo.get_incomplete_dependency_count(task.id)
        assert count == 0

    async def test_find_waiting_dependents(self, db_session):
        """find_waiting_dependents returns waiting tasks that depend on given task."""
        project = await make_project(db_session)
        phase = await make_phase(db_session, project.id)
        task_done = await make_task(db_session, project.id, phase.id, status=TaskStatus.done)
        task_waiting = await make_task(db_session, project.id, phase.id, status=TaskStatus.pending)
        await db_session.commit()

        repo = TaskRepository(db_session)
        await repo.add_dependencies(task_waiting.id, [task_done.id])
        await db_session.commit()

        dependents = await repo.find_waiting_dependents(task_done.id)
        assert len(dependents) == 1
        assert dependents[0].id == task_waiting.id

    async def test_count_by_status(self, db_session):
        """count_by_status returns task counts grouped by status."""
        project = await make_project(db_session)
        phase = await make_phase(db_session, project.id)
        await make_task(db_session, project.id, phase.id, status=TaskStatus.pending)
        await make_task(db_session, project.id, phase.id, status=TaskStatus.pending)
        await make_task(db_session, project.id, phase.id, status=TaskStatus.done)
        await db_session.commit()

        repo = TaskRepository(db_session)
        counts = await repo.count_by_status(project.id)

        assert counts.get("pending") == 2
        assert counts.get("done") == 1

    async def test_list_ready_by_priority(self, db_session):
        """list_ready_by_priority sorts by priority (critical first)."""
        project = await make_project(db_session)
        phase = await make_phase(db_session, project.id)
        await make_task(db_session, project.id, phase.id, priority=TaskPriority.low)
        await make_task(db_session, project.id, phase.id, priority=TaskPriority.critical)
        await db_session.commit()

        repo = TaskRepository(db_session)
        tasks = await repo.list_ready_by_priority(project.id)

        assert len(tasks) == 2
        assert tasks[0].priority == TaskPriority.critical
        assert tasks[1].priority == TaskPriority.low

    async def test_count_active_tasks(self, db_session):
        """count_active_tasks counts in_progress/review tasks."""
        project = await make_project(db_session)
        phase = await make_phase(db_session, project.id)
        await make_task(db_session, project.id, phase.id, status=TaskStatus.running)
        await make_task(db_session, project.id, phase.id, status=TaskStatus.running)
        await make_task(db_session, project.id, phase.id, status=TaskStatus.done)
        await db_session.commit()

        repo = TaskRepository(db_session)
        count = await repo.count_active_tasks(project.id)
        assert count == 2

    async def test_list_waiting_in_phase(self, db_session):
        """list_waiting_in_phase returns only pending tasks (not done)."""
        project = await make_project(db_session)
        phase = await make_phase(db_session, project.id)
        await make_task(db_session, project.id, phase.id, status=TaskStatus.pending)
        await make_task(db_session, project.id, phase.id, status=TaskStatus.done)
        await db_session.commit()

        repo = TaskRepository(db_session)
        tasks = await repo.list_waiting_in_phase(phase.id)

        assert len(tasks) == 1
        assert tasks[0].status == TaskStatus.pending

    async def test_hard_delete(self, db_session):
        """hard_delete removes the task from the database."""
        project = await make_project(db_session)
        phase = await make_phase(db_session, project.id)
        task = await make_task(db_session, project.id, phase.id)
        await db_session.commit()

        repo = TaskRepository(db_session)
        await repo.hard_delete(task.id)
        await db_session.commit()

        result = await repo.get_by_id(task.id)
        assert result is None

    async def test_hard_delete_many(self, db_session):
        """hard_delete_many removes multiple tasks and returns count."""
        project = await make_project(db_session)
        phase = await make_phase(db_session, project.id)
        task_a = await make_task(db_session, project.id, phase.id)
        task_b = await make_task(db_session, project.id, phase.id)
        await db_session.commit()

        repo = TaskRepository(db_session)
        count = await repo.hard_delete_many([task_a.id, task_b.id])
        await db_session.commit()

        assert count == 2
        assert await repo.get_by_id(task_a.id) is None
        assert await repo.get_by_id(task_b.id) is None

    async def test_hard_delete_many_empty(self, db_session):
        """hard_delete_many with empty list returns 0."""
        repo = TaskRepository(db_session)
        count = await repo.hard_delete_many([])
        assert count == 0

    async def test_clear_dependencies(self, db_session):
        """clear_dependencies removes all dep links for a task."""
        project = await make_project(db_session)
        phase = await make_phase(db_session, project.id)
        task_a = await make_task(db_session, project.id, phase.id)
        task_b = await make_task(db_session, project.id, phase.id)
        await db_session.commit()

        repo = TaskRepository(db_session)
        await repo.add_dependencies(task_a.id, [task_b.id])
        await db_session.commit()

        await repo.clear_dependencies(task_a.id)
        await db_session.commit()

        dep_ids = await repo.get_dependency_ids(task_a.id)
        assert len(dep_ids) == 0


# -- PhaseRepository Tests ----------------------------------------------------


class TestPhaseRepository:
    async def test_get_next_order_empty(self, db_session):
        """get_next_order returns 1 when no phases exist."""
        project = await make_project(db_session)
        await db_session.commit()

        repo = PhaseRepository(db_session)
        order = await repo.get_next_order(project.id)
        assert order == 1

    async def test_get_next_order_with_phases(self, db_session):
        """get_next_order returns max order + 1."""
        project = await make_project(db_session)
        await make_phase(db_session, project.id, order=3)
        await db_session.commit()

        repo = PhaseRepository(db_session)
        order = await repo.get_next_order(project.id)
        assert order == 4

    async def test_get_active_phase(self, db_session):
        """get_active_phase returns the active phase."""
        project = await make_project(db_session)
        active = await make_phase(db_session, project.id, order=1, status=PhaseStatus.active)
        await make_phase(db_session, project.id, order=2, status=PhaseStatus.pending)
        await db_session.commit()

        repo = PhaseRepository(db_session)
        result = await repo.get_active_phase(project.id)
        assert result is not None
        assert result.id == active.id

    async def test_get_active_phase_none(self, db_session):
        """get_active_phase returns None when no active phase."""
        project = await make_project(db_session)
        await make_phase(db_session, project.id, order=1, status=PhaseStatus.pending)
        await db_session.commit()

        repo = PhaseRepository(db_session)
        result = await repo.get_active_phase(project.id)
        assert result is None

    async def test_get_first_pending_phase(self, db_session):
        """get_first_pending_phase returns the pending phase with lowest order."""
        project = await make_project(db_session)
        await make_phase(db_session, project.id, order=1, status=PhaseStatus.active)
        second = await make_phase(db_session, project.id, order=2, status=PhaseStatus.pending)
        await make_phase(db_session, project.id, order=3, status=PhaseStatus.pending)
        await db_session.commit()

        repo = PhaseRepository(db_session)
        result = await repo.get_first_pending_phase(project.id)
        assert result is not None
        assert result.id == second.id

    async def test_get_next_pending_phase(self, db_session):
        """get_next_pending_phase returns pending phase after current_order."""
        project = await make_project(db_session)
        await make_phase(db_session, project.id, order=1, status=PhaseStatus.active)
        await make_phase(db_session, project.id, order=2, status=PhaseStatus.pending)
        third = await make_phase(db_session, project.id, order=3, status=PhaseStatus.pending)
        await db_session.commit()

        repo = PhaseRepository(db_session)
        result = await repo.get_next_pending_phase(project.id, current_order=2)
        assert result is not None
        assert result.id == third.id

    async def test_get_next_pending_phase_none(self, db_session):
        """get_next_pending_phase returns None when no pending phase after current."""
        project = await make_project(db_session)
        await make_phase(db_session, project.id, order=1, status=PhaseStatus.active)
        await db_session.commit()

        repo = PhaseRepository(db_session)
        result = await repo.get_next_pending_phase(project.id, current_order=1)
        assert result is None

    async def test_count_incomplete_tasks(self, db_session):
        """count_incomplete_tasks counts non-done tasks in a phase."""
        project = await make_project(db_session)
        phase = await make_phase(db_session, project.id)
        await make_task(db_session, project.id, phase.id, status=TaskStatus.pending)
        await make_task(db_session, project.id, phase.id, status=TaskStatus.running)
        await make_task(db_session, project.id, phase.id, status=TaskStatus.done)
        await db_session.commit()

        repo = PhaseRepository(db_session)
        count = await repo.count_incomplete_tasks(phase.id)
        assert count == 2


# -- redis_client Tests -------------------------------------------------------


async def test_get_redis_initializes_client():
    """get_redis creates and caches a Redis client."""
    import backend.src.storage.redis_client as redis_module

    original = redis_module.redis_client
    redis_module.redis_client = None
    try:
        with patch("redis.asyncio.from_url") as mock_from_url:
            mock_redis = AsyncMock()
            mock_from_url.return_value = mock_redis

            from backend.src.storage.redis_client import get_redis

            client = await get_redis()
            assert client is mock_redis
            mock_from_url.assert_called_once()

            # Second call reuses cached client
            client2 = await get_redis()
            assert client2 is mock_redis
            assert mock_from_url.call_count == 1  # not called again
    finally:
        redis_module.redis_client = original


async def test_close_redis_cleans_up():
    """close_redis closes the client and resets global."""
    import backend.src.storage.redis_client as redis_module

    mock_redis = AsyncMock()
    redis_module.redis_client = mock_redis
    try:
        from backend.src.storage.redis_client import close_redis

        await close_redis()
        mock_redis.close.assert_awaited_once()
        assert redis_module.redis_client is None
    finally:
        redis_module.redis_client = None


async def test_close_redis_noop_when_none():
    """close_redis is a no-op when no client exists."""
    import backend.src.storage.redis_client as redis_module

    redis_module.redis_client = None
    from backend.src.storage.redis_client import close_redis

    await close_redis()  # Should not raise
    assert redis_module.redis_client is None


# -- ProjectRepository Tests --------------------------------------------------


class TestProjectRepository:
    async def test_list_all(self, db_session):
        """list_all returns all projects."""
        from backend.src.repositories.project_repository import ProjectRepository

        project = await make_project(db_session)
        await db_session.commit()

        repo = ProjectRepository(db_session)
        results = await repo.list_all()

        assert any(p.id == project.id for p in results)

    async def test_exists_true(self, db_session):
        """exists returns True for an existing project."""
        from backend.src.repositories.project_repository import ProjectRepository

        project = await make_project(db_session)
        await db_session.commit()

        repo = ProjectRepository(db_session)
        assert await repo.exists(project.id) is True

    async def test_exists_false(self, db_session):
        """exists returns False for a non-existent project."""
        from backend.src.repositories.project_repository import ProjectRepository

        repo = ProjectRepository(db_session)
        assert await repo.exists(uuid.uuid4()) is False


# -- TaskRepository additional coverage --------------------------------------


class TestTaskRepositoryEdgeCases:
    async def test_validate_dependencies_exist_with_existing_task(self, db_session):
        """validate_dependencies_exist with an existing task returns empty list."""
        project = await make_project(db_session)
        phase = await make_phase(db_session, project.id)
        task = await make_task(db_session, project.id, phase.id)
        await db_session.commit()

        repo = TaskRepository(db_session)
        missing = await repo.validate_dependencies_exist([task.id])
        assert missing == []

    async def test_detect_circular_dependency_diamond_pattern(self, db_session):
        """detect_circular_dependency uses visited set correctly (diamond A→C, B→C)."""
        project = await make_project(db_session)
        phase = await make_phase(db_session, project.id)
        task_a = await make_task(db_session, project.id, phase.id)
        task_b = await make_task(db_session, project.id, phase.id)
        task_c = await make_task(db_session, project.id, phase.id)
        task_x = await make_task(db_session, project.id, phase.id)
        await db_session.commit()

        repo = TaskRepository(db_session)
        # A depends on C, B depends on C (diamond: X→[A,B], both→C)
        await repo.add_dependencies(task_a.id, [task_c.id])
        await repo.add_dependencies(task_b.id, [task_c.id])
        await db_session.commit()

        # X depending on [A, B] is not circular, but DFS visits C twice (hits visited set)
        is_circular = await repo.detect_circular_dependency(task_x.id, [task_a.id, task_b.id])
        assert is_circular is False


