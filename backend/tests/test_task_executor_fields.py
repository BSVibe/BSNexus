"""Tests for executor_type and executor_metadata fields on Task model and schemas.

TDD: These tests are written BEFORE implementation (TASK-004).
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.models import Task, TaskStatus, TaskPriority
from backend.src.schemas import TaskCreate, TaskResponse, TaskUpdate


# ── Model Tests ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_task_executor_type_defaults_to_coding(db_session: AsyncSession):
    """New tasks without explicit executor_type should default to 'coding'."""
    project_id, phase_id = await _create_project_and_phase(db_session)

    task = Task(
        project_id=project_id,
        phase_id=phase_id,
        title="Test task",
        description="desc",
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    assert task.executor_type == "coding"


@pytest.mark.asyncio
async def test_task_executor_type_can_be_set(db_session: AsyncSession):
    """Tasks can have a custom executor_type."""
    project_id, phase_id = await _create_project_and_phase(db_session)

    task = Task(
        project_id=project_id,
        phase_id=phase_id,
        title="Refactor task",
        executor_type="refactor",
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    assert task.executor_type == "refactor"


@pytest.mark.asyncio
async def test_task_executor_metadata_defaults_to_empty_dict(db_session: AsyncSession):
    """New tasks without explicit executor_metadata should default to empty dict."""
    project_id, phase_id = await _create_project_and_phase(db_session)

    task = Task(
        project_id=project_id,
        phase_id=phase_id,
        title="Test task",
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    assert task.executor_metadata == {}


@pytest.mark.asyncio
async def test_task_executor_metadata_stores_json(db_session: AsyncSession):
    """Task executor_metadata stores arbitrary JSON data."""
    project_id, phase_id = await _create_project_and_phase(db_session)

    meta = {"executor_config": {"timeout": 300}, "tags": ["urgent", "backend"]}
    task = Task(
        project_id=project_id,
        phase_id=phase_id,
        title="Task with metadata",
        executor_metadata=meta,
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    assert task.executor_metadata == meta
    assert task.executor_metadata["executor_config"]["timeout"] == 300


@pytest.mark.asyncio
async def test_task_persists_executor_type_and_metadata(db_session: AsyncSession):
    """Both fields survive a round-trip to the database."""
    project_id, phase_id = await _create_project_and_phase(db_session)

    task = Task(
        project_id=project_id,
        phase_id=phase_id,
        title="Persisted task",
        executor_type="bugfix",
        executor_metadata={"priority_override": True},
    )
    db_session.add(task)
    await db_session.commit()

    result = await db_session.execute(select(Task).where(Task.id == task.id))
    loaded = result.scalar_one()

    assert loaded.executor_type == "bugfix"
    assert loaded.executor_metadata == {"priority_override": True}


# ── Schema Tests ─────────────────────────────────────────────────────────


def test_task_create_schema_executor_type_default():
    """TaskCreate defaults executor_type to 'coding'."""
    data = TaskCreate(
        project_id=uuid.uuid4(),
        phase_id=uuid.uuid4(),
        title="Test",
        description="desc",
        priority=TaskPriority.medium,
        worker_prompt="do work",
        qa_prompt="check work",
    )
    assert data.executor_type == "coding"


def test_task_create_schema_executor_type_custom():
    """TaskCreate accepts custom executor_type."""
    data = TaskCreate(
        project_id=uuid.uuid4(),
        phase_id=uuid.uuid4(),
        title="Test",
        description="desc",
        priority=TaskPriority.medium,
        executor_type="refactor",
        worker_prompt="do work",
        qa_prompt="check work",
    )
    assert data.executor_type == "refactor"


def test_task_create_schema_executor_metadata_default():
    """TaskCreate defaults executor_metadata to empty dict."""
    data = TaskCreate(
        project_id=uuid.uuid4(),
        phase_id=uuid.uuid4(),
        title="Test",
        description="desc",
        priority=TaskPriority.medium,
        worker_prompt="do work",
        qa_prompt="check work",
    )
    assert data.executor_metadata == {}


def test_task_create_schema_executor_metadata_custom():
    """TaskCreate accepts custom executor_metadata."""
    meta = {"env": "staging"}
    data = TaskCreate(
        project_id=uuid.uuid4(),
        phase_id=uuid.uuid4(),
        title="Test",
        description="desc",
        priority=TaskPriority.medium,
        executor_metadata=meta,
        worker_prompt="do work",
        qa_prompt="check work",
    )
    assert data.executor_metadata == meta


def test_task_response_schema_executor_type_default():
    """TaskResponse defaults executor_type to 'coding' for backward compat."""
    resp = TaskResponse(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        phase_id=uuid.uuid4(),
        title="Test",
        status=TaskStatus.pending,
        priority=TaskPriority.medium,
        version=1,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    assert resp.executor_type == "coding"


def test_task_response_schema_executor_metadata_default():
    """TaskResponse defaults executor_metadata to empty dict for backward compat."""
    resp = TaskResponse(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        phase_id=uuid.uuid4(),
        title="Test",
        status=TaskStatus.pending,
        priority=TaskPriority.medium,
        version=1,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    assert resp.executor_metadata == {}


def test_task_update_schema_accepts_executor_type():
    """TaskUpdate can include executor_type."""
    data = TaskUpdate(executor_type="test")
    assert data.executor_type == "test"


def test_task_update_schema_executor_type_optional():
    """TaskUpdate executor_type is optional (None by default)."""
    data = TaskUpdate()
    assert data.executor_type is None


def test_task_update_schema_accepts_executor_metadata():
    """TaskUpdate can include executor_metadata."""
    data = TaskUpdate(executor_metadata={"key": "value"})
    assert data.executor_metadata == {"key": "value"}


def test_task_update_schema_executor_metadata_optional():
    """TaskUpdate executor_metadata is optional (None by default)."""
    data = TaskUpdate()
    assert data.executor_metadata is None


# ── Helpers ──────────────────────────────────────────────────────────────


async def _create_project_and_phase(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    """Create a project and phase for FK constraints, return their IDs."""
    from backend.src.models import Project, Phase

    project = Project(
        name="Test Project",
        description="For testing",
        repo_path="/tmp/test-repo",
    )
    session.add(project)
    await session.flush()

    phase = Phase(
        project_id=project.id,
        name="Phase 1",
        branch_name="feat/test",
        order=1,
    )
    session.add(phase)
    await session.flush()

    return project.id, phase.id
