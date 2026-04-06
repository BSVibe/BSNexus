"""Tests for Remote Worker Redis Streams dispatch protocol."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.worker_dispatch import WorkerDispatcher
from backend.src.models import Agent, Task, Tenant
from backend.src.models._legacy import TaskStatus, TaskPriority, TaskType, TaskSource
from backend.src.models.worker import Worker

_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")
_PROJECT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
_PHASE_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")


@pytest_asyncio.fixture(autouse=True)
async def _seed(db_session):
    """Seed tenant, project, and phase."""
    from backend.src.models._legacy import Project, Phase, PhaseStatus
    t = Tenant(id=_TENANT_ID, name="Test", slug="test", owner_user_id="user-1")
    db_session.add(t)
    p = Project(id=_PROJECT_ID, name="TestProject", description="test", repo_path="/tmp/test")
    db_session.add(p)
    await db_session.flush()
    ph = Phase(id=_PHASE_ID, project_id=_PROJECT_ID, name="Phase 1", branch_name="main", order=0, status=PhaseStatus.active)
    db_session.add(ph)
    await db_session.flush()
    await db_session.commit()


async def _create_worker(db: AsyncSession, name: str = "worker-1", capabilities: list[str] | None = None) -> Worker:
    w = Worker(
        tenant_id=_TENANT_ID,
        name=name,
        labels=[],
        capabilities=capabilities or ["coding"],
        token_hash="hash123",
        status="online",
        last_heartbeat=datetime.now(timezone.utc),
    )
    db.add(w)
    await db.flush()
    await db.refresh(w)
    return w


async def _create_task(
    db: AsyncSession,
    title: str = "Test Task",
    status: TaskStatus = TaskStatus.ready,
    agent_id: uuid.UUID | None = None,
    executor_type: str = "coding",
) -> Task:
    task = Task(
        project_id=_PROJECT_ID,
        phase_id=_PHASE_ID,
        title=title,
        status=status,
        priority=TaskPriority.medium,
        task_type=TaskType.feature,
        source=TaskSource.architect,
        agent_id=agent_id,
        executor_type=executor_type,
    )
    db.add(task)
    await db.flush()
    await db.refresh(task)
    return task


@pytest.mark.asyncio
async def test_dispatch_task_to_worker(db_session):
    """Dispatcher publishes task to worker-specific stream."""
    worker = await _create_worker(db_session)
    task = await _create_task(db_session)
    await db_session.commit()

    mock_stream = AsyncMock()
    mock_stream.publish = AsyncMock(return_value="msg-1")

    dispatcher = WorkerDispatcher(mock_stream)
    await dispatcher.dispatch_task(worker.id, task.id, task.title, str(_PROJECT_ID))

    mock_stream.publish.assert_called_once()
    call_args = mock_stream.publish.call_args
    stream_name = call_args[0][0]
    data = call_args[0][1]

    assert stream_name == f"tasks:worker:{worker.id}"
    assert data["task_id"] == str(task.id)
    assert data["project_id"] == str(_PROJECT_ID)
    assert data["action"] == "execute"


@pytest.mark.asyncio
async def test_find_available_worker(db_session):
    """Find a worker matching the required capability."""
    w1 = await _create_worker(db_session, "worker-1", ["coding", "analysis"])
    w2 = await _create_worker(db_session, "worker-2", ["writing"])
    await db_session.commit()

    mock_stream = AsyncMock()
    dispatcher = WorkerDispatcher(mock_stream)

    worker = await dispatcher.find_available_worker(db_session, capability="coding")
    assert worker is not None
    assert worker.id == w1.id

    writer = await dispatcher.find_available_worker(db_session, capability="writing")
    assert writer is not None
    assert writer.id == w2.id


@pytest.mark.asyncio
async def test_find_available_worker_no_match(db_session):
    """Returns None when no worker has the capability."""
    await _create_worker(db_session, "worker-1", ["writing"])
    await db_session.commit()

    mock_stream = AsyncMock()
    dispatcher = WorkerDispatcher(mock_stream)

    worker = await dispatcher.find_available_worker(db_session, capability="research")
    assert worker is None


@pytest.mark.asyncio
async def test_report_result(db_session):
    """Worker result reporting publishes to result stream."""
    mock_stream = AsyncMock()
    mock_stream.publish = AsyncMock(return_value="msg-1")

    dispatcher = WorkerDispatcher(mock_stream)
    task_id = uuid.uuid4()
    worker_id = uuid.uuid4()

    await dispatcher.report_result(
        worker_id=worker_id,
        task_id=task_id,
        success=True,
        output_data={"diff": "some code"},
    )

    mock_stream.publish.assert_called_once()
    call_args = mock_stream.publish.call_args
    assert call_args[0][0] == "tasks:results"
    data = call_args[0][1]
    assert data["task_id"] == str(task_id)
    assert data["worker_id"] == str(worker_id)
    assert data["success"] == "true"
