"""Tests for the simplified 4-state TaskStateMachine."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.src.core.state_machine import TaskStateMachine
from backend.src.models import Task, TaskHistory, TaskPriority, TaskStatus



# -- Fixtures -----------------------------------------------------------------


@pytest.fixture
def state_machine() -> TaskStateMachine:
    return TaskStateMachine()


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    db.execute = AsyncMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def mock_stream() -> AsyncMock:
    manager = AsyncMock()
    manager.publish = AsyncMock(return_value="mock-id")
    manager.publish_board_event = AsyncMock()
    return manager


def make_task(status: TaskStatus = TaskStatus.pending, **kwargs) -> Task:
    """Construct a Task without touching the DB."""
    now = datetime.now(timezone.utc)
    return Task(
        id=kwargs.get("id", uuid.uuid4()),
        project_id=kwargs.get("project_id", uuid.uuid4()),
        phase_id=kwargs.get("phase_id", uuid.uuid4()),
        title=kwargs.get("title", "Test Task"),
        description=kwargs.get("description", None),
        status=status,
        priority=kwargs.get("priority", TaskPriority.medium),
        version=kwargs.get("version", 1),
        worker_prompt=kwargs.get("worker_prompt", None),
        qa_prompt=kwargs.get("qa_prompt", None),
        branch_name=kwargs.get("branch_name", None),
        commit_hash=kwargs.get("commit_hash", None),
        qa_result=kwargs.get("qa_result", None),
        output_path=kwargs.get("output_path", None),
        error_message=kwargs.get("error_message", None),
        retry_count=kwargs.get("retry_count", 0),
        max_retries=kwargs.get("max_retries", 3),
        qa_feedback_history=kwargs.get("qa_feedback_history", None),
        started_at=kwargs.get("started_at", None),
        completed_at=None,
        created_at=now,
        updated_at=now,
    )


# -- Transition matrix --------------------------------------------------------

VALID_TRANSITIONS = [
    (TaskStatus.pending, TaskStatus.running),
    (TaskStatus.pending, TaskStatus.blocked),
    (TaskStatus.running, TaskStatus.done),
    (TaskStatus.running, TaskStatus.pending),
    (TaskStatus.running, TaskStatus.blocked),
    (TaskStatus.blocked, TaskStatus.pending),
]


def test_can_transition_valid_pairs(state_machine: TaskStateMachine) -> None:
    for from_s, to_s in VALID_TRANSITIONS:
        assert state_machine.can_transition(from_s, to_s) is True, f"{from_s} -> {to_s} should be valid"


def test_can_transition_rejects_self_transitions(state_machine: TaskStateMachine) -> None:
    """Self-transitions are not allowed in the simplified state machine."""
    for status in TaskStatus:
        assert state_machine.can_transition(status, status) is False, f"{status} -> {status} should be invalid"


def test_done_is_terminal(state_machine: TaskStateMachine) -> None:
    """done has no outgoing transitions."""
    for status in TaskStatus:
        assert state_machine.can_transition(TaskStatus.done, status) is False


def test_can_transition_rejects_invalid_pairs(state_machine: TaskStateMachine) -> None:
    invalid_pairs = [
        (TaskStatus.pending, TaskStatus.done),  # must go through running
        (TaskStatus.blocked, TaskStatus.running),
        (TaskStatus.blocked, TaskStatus.done),
        (TaskStatus.done, TaskStatus.pending),
        (TaskStatus.done, TaskStatus.running),
        (TaskStatus.done, TaskStatus.blocked),
    ]
    for from_s, to_s in invalid_pairs:
        assert state_machine.can_transition(from_s, to_s) is False, f"{from_s} -> {to_s} should be invalid"


# -- Valid transitions: side effects ------------------------------------------


async def test_pending_to_running_sets_started_at(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    task = make_task(status=TaskStatus.pending)
    await state_machine.transition(task, TaskStatus.running, db_session=mock_db, stream_manager=mock_stream)
    assert task.status == TaskStatus.running
    assert task.started_at is not None
    assert task.version == 2


async def test_running_to_done_sets_completed_at_and_promotes_dependents(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    task = make_task(status=TaskStatus.running)
    with patch("backend.src.core.state_machine.TaskRepository") as MockRepo:
        mock_repo = AsyncMock()
        mock_repo.find_waiting_dependents = AsyncMock(return_value=[])
        MockRepo.return_value = mock_repo
        await state_machine.transition(task, TaskStatus.done, db_session=mock_db, stream_manager=mock_stream)
    assert task.status == TaskStatus.done
    assert task.completed_at is not None


async def test_running_to_pending_resets_execution_fields(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    """Retry path: running -> pending clears error/qa/started_at."""
    task = make_task(
        status=TaskStatus.running,
        error_message="Build failed",
        qa_result={"passed": False},
        started_at=datetime.now(timezone.utc),
    )
    await state_machine.transition(
        task, TaskStatus.pending, db_session=mock_db, stream_manager=mock_stream, reason="Retry"
    )
    assert task.status == TaskStatus.pending
    assert task.error_message is None
    assert task.qa_result is None
    assert task.started_at is None


async def test_pending_to_pending_does_not_clear_fields(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    """blocked -> pending should NOT touch error/qa fields (caller decides)."""
    task = make_task(status=TaskStatus.blocked, error_message="Stuck", qa_result={"passed": False})
    await state_machine.transition(task, TaskStatus.pending, db_session=mock_db, stream_manager=mock_stream)
    assert task.status == TaskStatus.pending
    # Only the running -> pending transition resets fields
    assert task.error_message == "Stuck"
    assert task.qa_result == {"passed": False}


async def test_running_to_blocked_publishes_escalation(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    task = make_task(
        status=TaskStatus.running,
        retry_count=3,
        qa_feedback_history=[{"attempt": 1, "feedback": "bad"}],
    )
    await state_machine.transition(
        task,
        TaskStatus.blocked,
        db_session=mock_db,
        stream_manager=mock_stream,
        reason="Max retries exceeded",
    )
    assert task.status == TaskStatus.blocked
    assert task.error_message == "Max retries exceeded"
    # Find the publish call to tasks:escalation
    escalation_call = None
    for call in mock_stream.publish.call_args_list:
        if call[0][0] == "tasks:escalation":
            escalation_call = call
            break
    assert escalation_call is not None
    payload = escalation_call[0][1]
    assert payload["task_id"] == str(task.id)
    assert payload["error_message"] == "Max retries exceeded"
    assert json.loads(payload["qa_feedback_history"]) == [{"attempt": 1, "feedback": "bad"}]


async def test_blocked_to_pending(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    task = make_task(status=TaskStatus.blocked)
    await state_machine.transition(
        task, TaskStatus.pending, db_session=mock_db, stream_manager=mock_stream, reason="Unblocked"
    )
    assert task.status == TaskStatus.pending
    assert task.version == 2


# -- Invalid transitions ------------------------------------------------------


async def test_invalid_pending_to_done_raises(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    task = make_task(status=TaskStatus.pending)
    with pytest.raises(ValueError, match="Invalid transition"):
        await state_machine.transition(task, TaskStatus.done, db_session=mock_db, stream_manager=mock_stream)


async def test_invalid_done_to_pending_raises(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    task = make_task(status=TaskStatus.done)
    with pytest.raises(ValueError, match="Invalid transition"):
        await state_machine.transition(task, TaskStatus.pending, db_session=mock_db, stream_manager=mock_stream)


async def test_invalid_blocked_to_running_raises(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    task = make_task(status=TaskStatus.blocked)
    with pytest.raises(ValueError, match="Invalid transition"):
        await state_machine.transition(task, TaskStatus.running, db_session=mock_db, stream_manager=mock_stream)


# -- Version + history --------------------------------------------------------


async def test_version_increments_per_transition(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    task = make_task(status=TaskStatus.pending, version=1)
    await state_machine.transition(task, TaskStatus.running, db_session=mock_db, stream_manager=mock_stream)
    assert task.version == 2
    await state_machine.transition(task, TaskStatus.pending, db_session=mock_db, stream_manager=mock_stream)
    assert task.version == 3


async def test_history_recorded(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    task = make_task(status=TaskStatus.pending)
    await state_machine.transition(
        task, TaskStatus.running, actor="test-user", reason="dispatched", db_session=mock_db, stream_manager=mock_stream
    )
    mock_db.add.assert_called_once()
    added_obj = mock_db.add.call_args[0][0]
    assert isinstance(added_obj, TaskHistory)
    assert added_obj.from_status == "pending"
    assert added_obj.to_status == "running"
    assert added_obj.actor == "test-user"
    assert added_obj.reason == "dispatched"


# -- Dependent promotion ------------------------------------------------------


async def test_promote_dependents_when_phase_active_and_deps_met(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    task = make_task(status=TaskStatus.running)
    pending_dependent = make_task(status=TaskStatus.pending, version=1)

    with (
        patch("backend.src.core.state_machine.TaskRepository") as MockRepo,
        patch.object(state_machine, "_is_phase_active", return_value=True),
    ):
        mock_repo = AsyncMock()
        mock_repo.find_waiting_dependents = AsyncMock(return_value=[pending_dependent])
        mock_repo.check_dependencies_met = AsyncMock(return_value=True)
        MockRepo.return_value = mock_repo
        await state_machine.transition(task, TaskStatus.done, db_session=mock_db, stream_manager=mock_stream)

    # Already pending, but version bumps because the promotion records history.
    assert pending_dependent.status == TaskStatus.pending
    assert pending_dependent.version == 2


async def test_promote_dependents_skips_when_phase_inactive(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    task = make_task(status=TaskStatus.running)
    pending_dependent = make_task(status=TaskStatus.pending, version=1)

    with (
        patch("backend.src.core.state_machine.TaskRepository") as MockRepo,
        patch.object(state_machine, "_is_phase_active", return_value=False),
    ):
        mock_repo = AsyncMock()
        mock_repo.find_waiting_dependents = AsyncMock(return_value=[pending_dependent])
        mock_repo.check_dependencies_met = AsyncMock(return_value=True)
        MockRepo.return_value = mock_repo
        await state_machine.transition(task, TaskStatus.done, db_session=mock_db, stream_manager=mock_stream)

    assert pending_dependent.version == 1  # not touched


async def test_promote_dependents_skips_when_deps_not_met(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    task = make_task(status=TaskStatus.running)
    pending_dependent = make_task(status=TaskStatus.pending, version=1)

    with (
        patch("backend.src.core.state_machine.TaskRepository") as MockRepo,
        patch.object(state_machine, "_is_phase_active", return_value=True),
    ):
        mock_repo = AsyncMock()
        mock_repo.find_waiting_dependents = AsyncMock(return_value=[pending_dependent])
        mock_repo.check_dependencies_met = AsyncMock(return_value=False)
        MockRepo.return_value = mock_repo
        await state_machine.transition(task, TaskStatus.done, db_session=mock_db, stream_manager=mock_stream)

    assert pending_dependent.version == 1  # not promoted


# -- Board event publication --------------------------------------------------


async def test_transition_publishes_board_event(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    task = make_task(status=TaskStatus.pending)
    await state_machine.transition(task, TaskStatus.running, db_session=mock_db, stream_manager=mock_stream)
    mock_stream.publish_board_event.assert_called_once()
    args, _ = mock_stream.publish_board_event.call_args
    assert args[0] == "task_transition"
    assert args[1]["from_status"] == "pending"
    assert args[1]["to_status"] == "running"
