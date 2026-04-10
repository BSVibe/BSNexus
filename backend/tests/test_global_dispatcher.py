"""Tests for the global dispatcher tick logic.

The tests drive ``GlobalDispatcher.tick`` against the test SQLite DB and
mock the worker dispatcher and Redis stream so we can assert exactly
which side effects fire.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.src.core.global_dispatcher import GlobalDispatcher
from backend.src.core.tenant_context import DEFAULT_TENANT_ID
from backend.src.models import (
    Phase,
    PhaseStatus,
    Project,
    ProjectStatus,
    Task,
    TaskPriority,
    TaskSource,
    TaskStatus,
    TaskType,
    Tenant,
    Worker,
)


@pytest.fixture(autouse=True)
async def _seed_default_tenant(db_session):
    db_session.add(
        Tenant(
            id=DEFAULT_TENANT_ID,
            name="Test Tenant",
            slug="test",
            owner_user_id="test-user",
        )
    )
    await db_session.commit()
    yield


@pytest.fixture
def stream_mock() -> AsyncMock:
    m = AsyncMock()
    m.publish = AsyncMock(return_value="msg-1")
    m.publish_project_event = AsyncMock()
    return m


async def _mk_project(db_session, *, status: ProjectStatus = ProjectStatus.active) -> Project:
    now = datetime.now(timezone.utc)
    project = Project(
        id=uuid.uuid4(),
        name="Dispatcher Project",
        description="",
        status=status,
        created_at=now,
        updated_at=now,
    )
    db_session.add(project)
    await db_session.flush()
    return project


async def _mk_phase(
    db_session, project: Project, *, order: int = 1, status: PhaseStatus = PhaseStatus.active
) -> Phase:
    now = datetime.now(timezone.utc)
    phase = Phase(
        id=uuid.uuid4(),
        project_id=project.id,
        name=f"Phase {order}",
        branch_name=f"phase-{order}",
        order=order,
        status=status,
        created_at=now,
        updated_at=now,
    )
    db_session.add(phase)
    await db_session.flush()
    return phase


async def _mk_task(
    db_session,
    project: Project,
    phase: Phase,
    *,
    title: str = "Task",
    status: TaskStatus = TaskStatus.pending,
) -> Task:
    now = datetime.now(timezone.utc)
    task = Task(
        id=uuid.uuid4(),
        project_id=project.id,
        phase_id=phase.id,
        title=title,
        status=status,
        priority=TaskPriority.medium,
        task_type=TaskType.feature,
        source=TaskSource.llm,
        version=1,
        worker_prompt={"prompt": f"{title} prompt"},
        created_at=now,
        updated_at=now,
    )
    db_session.add(task)
    await db_session.flush()
    return task


async def _mk_worker(db_session, *, status: str = "online") -> Worker:
    now = datetime.now(timezone.utc)
    worker = Worker(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="Worker A",
        token_hash="hash",
        is_active=True,
        status=status,
        last_heartbeat=now,
        created_at=now,
        updated_at=now,
    )
    db_session.add(worker)
    await db_session.flush()
    return worker


# ── Tests ────────────────────────────────────────────────────────────


async def test_tick_dispatches_pending_task_to_available_worker(db_session, stream_mock, monkeypatch):
    project = await _mk_project(db_session)
    phase = await _mk_phase(db_session, project)
    task = await _mk_task(db_session, project, phase, title="Build")
    worker = await _mk_worker(db_session)
    await db_session.commit()

    dispatcher = GlobalDispatcher(stream_mock)
    # Patch async_session so the dispatcher uses our test session.
    monkeypatch.setattr(
        "backend.src.core.global_dispatcher.async_session",
        _make_session_factory(db_session),
    )

    dispatched: list[dict] = []

    async def fake_dispatch_task(**kwargs):
        dispatched.append(kwargs)
        return "msg-1"

    dispatcher._worker_dispatcher.dispatch_task = fake_dispatch_task  # type: ignore[assignment]

    await dispatcher.tick()
    await db_session.refresh(task)

    assert task.status == TaskStatus.running
    assert len(dispatched) == 1
    assert dispatched[0]["worker_id"] == worker.id
    assert dispatched[0]["task_id"] == task.id
    assert dispatched[0]["prompt"] == "Build prompt"


async def test_tick_skips_dispatch_when_no_worker_available(db_session, stream_mock, monkeypatch):
    project = await _mk_project(db_session)
    phase = await _mk_phase(db_session, project)
    task = await _mk_task(db_session, project, phase)
    await db_session.commit()

    dispatcher = GlobalDispatcher(stream_mock)
    monkeypatch.setattr(
        "backend.src.core.global_dispatcher.async_session",
        _make_session_factory(db_session),
    )
    dispatcher._worker_dispatcher.dispatch_task = AsyncMock(return_value="msg-1")  # type: ignore[assignment]

    await dispatcher.tick()
    await db_session.refresh(task)
    assert task.status == TaskStatus.pending
    dispatcher._worker_dispatcher.dispatch_task.assert_not_called()  # type: ignore[union-attr]


async def test_tick_advances_phase_when_all_tasks_done(db_session, stream_mock, monkeypatch):
    project = await _mk_project(db_session)
    phase1 = await _mk_phase(db_session, project, order=1, status=PhaseStatus.active)
    phase2 = await _mk_phase(db_session, project, order=2, status=PhaseStatus.pending)
    await _mk_task(db_session, project, phase1, status=TaskStatus.done)
    await _mk_task(db_session, project, phase1, status=TaskStatus.done)
    await db_session.commit()

    dispatcher = GlobalDispatcher(stream_mock)
    monkeypatch.setattr(
        "backend.src.core.global_dispatcher.async_session",
        _make_session_factory(db_session),
    )
    dispatcher._worker_dispatcher.dispatch_task = AsyncMock(return_value="msg-1")  # type: ignore[assignment]

    await dispatcher.tick()
    await db_session.refresh(phase1)
    await db_session.refresh(phase2)

    assert phase1.status == PhaseStatus.completed
    assert phase2.status == PhaseStatus.active
    stream_mock.publish_project_event.assert_any_call(
        str(project.id),
        "phase_advanced",
        {"completed_phase_id": str(phase1.id), "next_phase_id": str(phase2.id)},
    )


async def test_tick_ignores_inactive_projects(db_session, stream_mock, monkeypatch):
    project = await _mk_project(db_session, status=ProjectStatus.paused)
    phase = await _mk_phase(db_session, project)
    task = await _mk_task(db_session, project, phase)
    await _mk_worker(db_session)
    await db_session.commit()

    dispatcher = GlobalDispatcher(stream_mock)
    monkeypatch.setattr(
        "backend.src.core.global_dispatcher.async_session",
        _make_session_factory(db_session),
    )
    dispatcher._worker_dispatcher.dispatch_task = AsyncMock(return_value="msg-1")  # type: ignore[assignment]

    await dispatcher.tick()
    await db_session.refresh(task)
    assert task.status == TaskStatus.pending
    dispatcher._worker_dispatcher.dispatch_task.assert_not_called()  # type: ignore[union-attr]


async def test_start_stop_idempotent(stream_mock):
    dispatcher = GlobalDispatcher(stream_mock)
    dispatcher.start()
    dispatcher.start()  # second call should be a no-op
    await dispatcher.stop()
    # Stopping again is harmless.
    await dispatcher.stop()


# ── Helpers ──────────────────────────────────────────────────────────


def _make_session_factory(session):
    """Wrap an existing AsyncSession so the dispatcher's `async with` works."""

    class _Wrapper:
        def __init__(self, sess):
            self._sess = sess

        async def __aenter__(self):
            return self._sess

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def factory():
        return _Wrapper(session)

    return factory
