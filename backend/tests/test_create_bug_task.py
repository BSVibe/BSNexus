"""Tests for PMOrchestrator._create_bug_task auto bug task creation."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.src.core.orchestrator import PMOrchestrator
from backend.src.models import Task, TaskPriority, TaskSource, TaskStatus, TaskType


def make_task(status: TaskStatus = TaskStatus.waiting, **kwargs) -> Task:
    now = datetime.now(timezone.utc)
    return Task(
        id=kwargs.get("id", uuid.uuid4()),
        project_id=kwargs.get("project_id", uuid.uuid4()),
        phase_id=kwargs.get("phase_id", uuid.uuid4()),
        title=kwargs.get("title", "Test Task"),
        description=kwargs.get("description", None),
        status=status,
        priority=kwargs.get("priority", TaskPriority.medium),
        task_type=kwargs.get("task_type", TaskType.feature),
        source=kwargs.get("source", TaskSource.architect),
        version=1,
        worker_prompt=kwargs.get("worker_prompt", None),
        qa_prompt=kwargs.get("qa_prompt", None),
        branch_name=kwargs.get("branch_name", "feature/test"),
        commit_hash=None,
        qa_result=None,
        output_path=None,
        error_message=kwargs.get("error_message", None),
        retry_count=kwargs.get("retry_count", 3),
        max_retries=kwargs.get("max_retries", 3),
        qa_feedback_history=kwargs.get("qa_feedback_history", ["fail1", "fail2"]),
        started_at=None,
        completed_at=None,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_create_bug_task_sets_correct_fields() -> None:
    """Bug task has critical priority, bug type, auto_bug source, parent_task_id."""
    mock_stream = AsyncMock()
    mock_stream.publish_board_event = AsyncMock()
    orchestrator = PMOrchestrator(
        stream_manager=mock_stream,
        task_runner=AsyncMock(),
        state_machine=AsyncMock(),
    )

    failed_task = make_task(
        status=TaskStatus.redesign,
        title="Broken Feature",
        retry_count=3,
        qa_feedback_history=["err1", "err2"],
    )

    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()

    bug_task = await orchestrator._create_bug_task(
        failed_task, mock_db, "NullPointerError", "runtime"
    )

    assert bug_task is not None
    assert bug_task.title == "Bug: Broken Feature"
    assert bug_task.priority == TaskPriority.critical
    assert bug_task.task_type == TaskType.bug
    assert bug_task.source == TaskSource.auto_bug
    assert bug_task.parent_task_id == failed_task.id
    assert bug_task.status == TaskStatus.ready
    assert bug_task.branch_name == failed_task.branch_name
    assert "NullPointerError" in bug_task.description
    assert "runtime" in bug_task.description

    mock_db.add.assert_called_once()
    mock_db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_bug_task_publishes_board_event() -> None:
    """Bug task creation publishes a board event with bug_task_created type."""
    mock_stream = AsyncMock()
    mock_stream.publish_board_event = AsyncMock()
    orchestrator = PMOrchestrator(
        stream_manager=mock_stream,
        task_runner=AsyncMock(),
        state_machine=AsyncMock(),
    )

    failed_task = make_task(status=TaskStatus.redesign)

    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()

    await orchestrator._create_bug_task(failed_task, mock_db, "error", "cat")

    mock_stream.publish_board_event.assert_awaited_once()
    call_args = mock_stream.publish_board_event.call_args
    assert call_args[0][0] == "bug_task_created"
    event_data = call_args[0][1]
    assert event_data["parent_task_id"] == str(failed_task.id)
    assert "project_id" in event_data


@pytest.mark.asyncio
async def test_create_bug_task_handles_exception() -> None:
    """If bug task creation fails, returns None without raising."""
    mock_stream = AsyncMock()
    orchestrator = PMOrchestrator(
        stream_manager=mock_stream,
        task_runner=AsyncMock(),
        state_machine=AsyncMock(),
    )

    failed_task = make_task(status=TaskStatus.redesign)

    mock_db = AsyncMock()
    mock_db.add = MagicMock(side_effect=Exception("DB error"))

    result = await orchestrator._create_bug_task(failed_task, mock_db, "error")
    assert result is None


@pytest.mark.asyncio
async def test_create_bug_task_worker_prompt_includes_error() -> None:
    """Worker prompt includes original error and failure history."""
    mock_stream = AsyncMock()
    mock_stream.publish_board_event = AsyncMock()
    orchestrator = PMOrchestrator(
        stream_manager=mock_stream,
        task_runner=AsyncMock(),
        state_machine=AsyncMock(),
    )

    failed_task = make_task(
        status=TaskStatus.redesign,
        title="Auth Bug",
        qa_feedback_history=["token expired", "retry failed"],
    )

    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()

    bug_task = await orchestrator._create_bug_task(
        failed_task, mock_db, "Auth token expired"
    )

    assert bug_task is not None
    assert "Auth token expired" in bug_task.worker_prompt["prompt"]
    assert "token expired" in bug_task.worker_prompt["prompt"]
    assert "Auth Bug" in bug_task.qa_prompt["prompt"]
