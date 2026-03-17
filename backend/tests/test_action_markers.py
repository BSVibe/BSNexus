"""Tests for architect action markers: _strip_action_markers and _execute_action_markers."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest

from backend.src.api.architect import _execute_action_markers, _strip_action_markers
from backend.src.models import (
    DesignSession,
    DesignSessionStatus,
    Phase,
    PhaseStatus,
    Project,
    ProjectStatus,
    Task,
    TaskPriority,
    TaskSource,
    TaskStatus,
    TaskType,
)


# ── _strip_action_markers ────────────────────────────────────────────


class TestStripActionMarkers:
    def test_strips_create_task_block(self) -> None:
        text = 'Hello [CREATE_TASK]{"title":"t"}[/CREATE_TASK] world'
        assert _strip_action_markers(text) == "Hello  world"

    def test_strips_modify_task_block(self) -> None:
        text = 'Before [MODIFY_TASK]{"task_id":"x"}[/MODIFY_TASK] after'
        assert _strip_action_markers(text) == "Before  after"

    def test_strips_multiple_blocks(self) -> None:
        text = (
            'A [CREATE_TASK]{"title":"t1"}[/CREATE_TASK] '
            'B [MODIFY_TASK]{"task_id":"x"}[/MODIFY_TASK] C'
        )
        result = _strip_action_markers(text)
        assert "[CREATE_TASK]" not in result
        assert "[MODIFY_TASK]" not in result
        assert "A" in result
        assert "B" in result
        assert "C" in result

    def test_no_markers_returns_unchanged(self) -> None:
        text = "No markers here"
        assert _strip_action_markers(text) == text

    def test_empty_string(self) -> None:
        assert _strip_action_markers("") == ""


# ── _execute_action_markers ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_create_task(db_session) -> None:
    """CREATE_TASK marker creates a task in the active phase."""
    now = datetime.now(timezone.utc)
    project = Project(
        id=uuid.uuid4(),
        name="Test Project",
        description="desc",
        repo_path="/test",
        status=ProjectStatus.active,
        created_at=now,
        updated_at=now,
    )
    db_session.add(project)

    phase = Phase(
        id=uuid.uuid4(),
        project_id=project.id,
        name="Phase 1",
        order=1,
        status=PhaseStatus.active,
        branch_name="feature/test",
        created_at=now,
        updated_at=now,
    )
    db_session.add(phase)
    await db_session.flush()

    session = DesignSession(
        id=uuid.uuid4(),
        status=DesignSessionStatus.project_bound,
        project_id=project.id,
        llm_config={},
        created_at=now,
        updated_at=now,
    )
    db_session.add(session)
    await db_session.flush()

    task_json = json.dumps({
        "title": "New Feature",
        "description": "Build something",
        "priority": "high",
        "task_type": "bug",
        "worker_prompt": "Do the work",
        "qa_prompt": "Check the work",
    })
    text = f"Here is a task: [CREATE_TASK]{task_json}[/CREATE_TASK]"

    actions = await _execute_action_markers(text, session, db_session)
    await db_session.commit()

    assert len(actions) == 1
    assert actions[0]["type"] == "task_created"
    assert actions[0]["title"] == "New Feature"

    # Verify task was created in DB
    from sqlalchemy import select
    result = await db_session.execute(select(Task).where(Task.project_id == project.id))
    tasks = list(result.scalars().all())
    assert len(tasks) == 1
    task = tasks[0]
    assert task.title == "New Feature"
    assert task.priority == TaskPriority.high
    assert task.task_type == TaskType.bug
    assert task.source == TaskSource.architect
    assert task.status == TaskStatus.ready
    assert task.branch_name == "feature/test"


@pytest.mark.asyncio
async def test_execute_create_task_no_project_id(db_session) -> None:
    """CREATE_TASK does nothing when session has no project_id."""
    now = datetime.now(timezone.utc)
    session = DesignSession(
        id=uuid.uuid4(),
        status=DesignSessionStatus.active,
        project_id=None,
        llm_config={},
        created_at=now,
        updated_at=now,
    )
    db_session.add(session)
    await db_session.flush()

    text = '[CREATE_TASK]{"title":"x"}[/CREATE_TASK]'
    actions = await _execute_action_markers(text, session, db_session)
    assert actions == []


@pytest.mark.asyncio
async def test_execute_create_task_invalid_json(db_session) -> None:
    """Invalid JSON in CREATE_TASK marker is silently skipped."""
    now = datetime.now(timezone.utc)
    project = Project(
        id=uuid.uuid4(), name="P", description="d", repo_path="/t",
        status=ProjectStatus.active, created_at=now, updated_at=now,
    )
    db_session.add(project)
    phase = Phase(
        id=uuid.uuid4(), project_id=project.id, name="Ph", order=1,
        status=PhaseStatus.active, branch_name="b", created_at=now, updated_at=now,
    )
    db_session.add(phase)
    await db_session.flush()

    session = DesignSession(
        id=uuid.uuid4(), status=DesignSessionStatus.project_bound,
        project_id=project.id, llm_config={}, created_at=now, updated_at=now,
    )
    db_session.add(session)
    await db_session.flush()

    text = "[CREATE_TASK]not valid json[/CREATE_TASK]"
    actions = await _execute_action_markers(text, session, db_session)
    assert actions == []


@pytest.mark.asyncio
async def test_execute_modify_task(db_session) -> None:
    """MODIFY_TASK marker updates an existing task."""
    now = datetime.now(timezone.utc)
    project = Project(
        id=uuid.uuid4(), name="P", description="d", repo_path="/t",
        status=ProjectStatus.active, created_at=now, updated_at=now,
    )
    db_session.add(project)
    phase = Phase(
        id=uuid.uuid4(), project_id=project.id, name="Ph", order=1,
        status=PhaseStatus.active, branch_name="b", created_at=now, updated_at=now,
    )
    db_session.add(phase)

    task = Task(
        id=uuid.uuid4(), project_id=project.id, phase_id=phase.id,
        title="Original Title", status=TaskStatus.ready, priority=TaskPriority.medium,
        task_type=TaskType.feature, source=TaskSource.architect,
        created_at=now, updated_at=now,
    )
    db_session.add(task)
    await db_session.flush()

    session = DesignSession(
        id=uuid.uuid4(), status=DesignSessionStatus.project_bound,
        project_id=project.id, llm_config={}, created_at=now, updated_at=now,
    )
    db_session.add(session)
    await db_session.flush()

    modify_json = json.dumps({
        "task_id": str(task.id),
        "title": "Updated Title",
        "priority": "critical",
    })
    text = f"[MODIFY_TASK]{modify_json}[/MODIFY_TASK]"

    actions = await _execute_action_markers(text, session, db_session)
    assert len(actions) == 1
    assert actions[0]["type"] == "task_modified"
    assert actions[0]["title"] == "Updated Title"

    # Re-fetch task from DB since _execute_action_markers uses TaskRepository
    from sqlalchemy import select
    result = await db_session.execute(select(Task).where(Task.id == task.id))
    updated_task = result.scalar_one()
    assert updated_task.title == "Updated Title"
    assert updated_task.priority == TaskPriority.critical


@pytest.mark.asyncio
async def test_execute_modify_task_rejects_in_progress(db_session) -> None:
    """MODIFY_TASK skips tasks that are in_progress."""
    now = datetime.now(timezone.utc)
    project = Project(
        id=uuid.uuid4(), name="P", description="d", repo_path="/t",
        status=ProjectStatus.active, created_at=now, updated_at=now,
    )
    db_session.add(project)
    phase = Phase(
        id=uuid.uuid4(), project_id=project.id, name="Ph", order=1,
        status=PhaseStatus.active, branch_name="b", created_at=now, updated_at=now,
    )
    db_session.add(phase)

    task = Task(
        id=uuid.uuid4(), project_id=project.id, phase_id=phase.id,
        title="In Progress Task", status=TaskStatus.in_progress,
        priority=TaskPriority.medium, task_type=TaskType.feature,
        source=TaskSource.architect, created_at=now, updated_at=now,
    )
    db_session.add(task)
    await db_session.flush()

    session = DesignSession(
        id=uuid.uuid4(), status=DesignSessionStatus.project_bound,
        project_id=project.id, llm_config={}, created_at=now, updated_at=now,
    )
    db_session.add(session)
    await db_session.flush()

    modify_json = json.dumps({"task_id": str(task.id), "title": "Should Not Change"})
    text = f"[MODIFY_TASK]{modify_json}[/MODIFY_TASK]"

    actions = await _execute_action_markers(text, session, db_session)
    assert actions == []

    await db_session.refresh(task)
    assert task.title == "In Progress Task"
