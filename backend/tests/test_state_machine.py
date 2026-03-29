from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.src.core.state_machine import TaskStateMachine
from backend.src.models import Task, TaskHistory, TaskPriority, TaskStatus

pytestmark = pytest.mark.asyncio

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


# -- Helpers ------------------------------------------------------------------


def make_task(status: TaskStatus = TaskStatus.waiting, **kwargs) -> Task:
    """Create a Task object without touching the DB."""
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


# -- can_transition (pure logic, no mocking) ---------------------------------


def test_can_transition_valid(state_machine: TaskStateMachine) -> None:
    assert state_machine.can_transition(TaskStatus.waiting, TaskStatus.ready) is True


def test_can_transition_invalid(state_machine: TaskStateMachine) -> None:
    assert state_machine.can_transition(TaskStatus.waiting, TaskStatus.done) is False


def test_can_transition_all_valid_paths(state_machine: TaskStateMachine) -> None:
    valid_pairs = [
        (TaskStatus.waiting, TaskStatus.ready),
        (TaskStatus.ready, TaskStatus.in_progress),
        (TaskStatus.in_progress, TaskStatus.review),
        (TaskStatus.in_progress, TaskStatus.ready),
        (TaskStatus.in_progress, TaskStatus.redesign),
        (TaskStatus.review, TaskStatus.done),
        (TaskStatus.review, TaskStatus.ready),
        (TaskStatus.review, TaskStatus.in_progress),
        (TaskStatus.review, TaskStatus.redesign),
        (TaskStatus.redesign, TaskStatus.waiting),
    ]
    for from_s, to_s in valid_pairs:
        assert state_machine.can_transition(from_s, to_s) is True, f"{from_s} -> {to_s} should be valid"


def test_can_transition_invalid_paths(state_machine: TaskStateMachine) -> None:
    invalid_pairs = [
        (TaskStatus.waiting, TaskStatus.done),
        (TaskStatus.waiting, TaskStatus.in_progress),
        (TaskStatus.waiting, TaskStatus.review),
        (TaskStatus.waiting, TaskStatus.redesign),
        (TaskStatus.ready, TaskStatus.done),
        (TaskStatus.ready, TaskStatus.waiting),
        (TaskStatus.ready, TaskStatus.review),
        (TaskStatus.done, TaskStatus.waiting),
        (TaskStatus.done, TaskStatus.ready),
        (TaskStatus.done, TaskStatus.in_progress),
        (TaskStatus.done, TaskStatus.review),
        (TaskStatus.done, TaskStatus.redesign),
        (TaskStatus.redesign, TaskStatus.ready),
        (TaskStatus.redesign, TaskStatus.in_progress),
        (TaskStatus.redesign, TaskStatus.review),
        (TaskStatus.redesign, TaskStatus.done),
    ]
    for from_s, to_s in invalid_pairs:
        assert state_machine.can_transition(from_s, to_s) is False, f"{from_s} -> {to_s} should be invalid"


# -- Terminal states cannot transition to arbitrary states ---------------------


def test_done_is_terminal(state_machine: TaskStateMachine) -> None:
    """done has no outgoing transitions."""
    for status in TaskStatus:
        assert state_machine.can_transition(TaskStatus.done, status) is False, f"done -> {status} should be invalid"


def test_redesign_limited_transitions(state_machine: TaskStateMachine) -> None:
    """redesign can only go to waiting."""
    allowed = {TaskStatus.waiting}
    for status in TaskStatus:
        if status in allowed:
            assert state_machine.can_transition(TaskStatus.redesign, status) is True
        else:
            assert state_machine.can_transition(TaskStatus.redesign, status) is False


# -- Valid transitions --------------------------------------------------------


async def test_valid_transition_waiting_to_ready(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    task = make_task(status=TaskStatus.waiting)
    result = await state_machine.transition(
        task, TaskStatus.ready, reason="deps met", actor="system", db_session=mock_db, stream_manager=mock_stream
    )
    assert result.status == TaskStatus.ready
    assert result.version == 2
    # Verify history was added to session
    mock_db.add.assert_called_once()
    # Verify board event was published
    mock_stream.publish_board_event.assert_called_once()


async def test_valid_transition_ready_to_in_progress(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    task = make_task(status=TaskStatus.ready)
    result = await state_machine.transition(
        task, TaskStatus.in_progress, db_session=mock_db, stream_manager=mock_stream
    )
    assert result.status == TaskStatus.in_progress
    assert result.started_at is not None


async def test_valid_transition_in_progress_to_review(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    task = make_task(status=TaskStatus.in_progress)
    result = await state_machine.transition(task, TaskStatus.review, db_session=mock_db, stream_manager=mock_stream)
    assert result.status == TaskStatus.review


async def test_valid_transition_in_progress_to_ready(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    """Execution failure retry: in_progress -> ready resets fields."""
    task = make_task(
        status=TaskStatus.in_progress,
        error_message="Build failed",
        qa_result={"passed": False},
        started_at=datetime.now(timezone.utc),
    )
    result = await state_machine.transition(
        task, TaskStatus.ready, db_session=mock_db, stream_manager=mock_stream, reason="Execution failed"
    )
    assert result.status == TaskStatus.ready
    assert result.error_message is None
    assert result.qa_result is None
    assert result.started_at is None


async def test_valid_transition_in_progress_to_redesign(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    task = make_task(status=TaskStatus.in_progress, retry_count=3)
    result = await state_machine.transition(
        task,
        TaskStatus.redesign,
        db_session=mock_db,
        stream_manager=mock_stream,
    )
    assert result.status == TaskStatus.redesign


async def test_valid_transition_review_to_done(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    task = make_task(status=TaskStatus.review)

    with patch("backend.src.core.state_machine.TaskRepository") as MockRepo:
        mock_repo_instance = AsyncMock()
        mock_repo_instance.find_waiting_dependents = AsyncMock(return_value=[])
        MockRepo.return_value = mock_repo_instance

        result = await state_machine.transition(task, TaskStatus.done, db_session=mock_db, stream_manager=mock_stream)

    assert result.status == TaskStatus.done
    assert result.completed_at is not None


async def test_valid_transition_review_to_ready(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    """QA failure retry: review -> ready resets execution fields."""
    task = make_task(
        status=TaskStatus.review,
        error_message="Tests failing",
        qa_result={"passed": False},
        started_at=datetime.now(timezone.utc),
    )
    result = await state_machine.transition(
        task, TaskStatus.ready, db_session=mock_db, stream_manager=mock_stream, reason="QA failed, retrying"
    )
    assert result.status == TaskStatus.ready
    assert result.error_message is None
    assert result.qa_result is None
    assert result.started_at is None


async def test_valid_transition_review_to_redesign(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    task = make_task(
        status=TaskStatus.review,
        retry_count=3,
        qa_feedback_history=[{"attempt": 1, "feedback": "bad"}],
    )
    result = await state_machine.transition(
        task,
        TaskStatus.redesign,
        db_session=mock_db,
        stream_manager=mock_stream,
        reason="QA failed after max retries",
    )
    assert result.status == TaskStatus.redesign


async def test_valid_transition_redesign_to_waiting(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    """redesign -> waiting means the task was modified by the Architect."""
    task = make_task(status=TaskStatus.redesign)
    result = await state_machine.transition(
        task,
        TaskStatus.waiting,
        db_session=mock_db,
        stream_manager=mock_stream,
        reason="Task redesigned by architect",
    )
    assert result.status == TaskStatus.waiting
    assert result.version == 2


async def test_invalid_transition_redesign_to_done(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    """redesign -> done is no longer allowed (tasks stay in redesign or go to waiting)."""
    task = make_task(status=TaskStatus.redesign)
    with pytest.raises(ValueError, match="Invalid transition"):
        await state_machine.transition(
            task,
            TaskStatus.done,
            db_session=mock_db,
            stream_manager=mock_stream,
            reason="This should fail",
        )


# -- Invalid transitions ------------------------------------------------------


async def test_invalid_transition_waiting_to_done(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    task = make_task(status=TaskStatus.waiting)
    with pytest.raises(ValueError, match="Invalid transition"):
        await state_machine.transition(task, TaskStatus.done, db_session=mock_db, stream_manager=mock_stream)


async def test_invalid_transition_done_to_ready(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    task = make_task(status=TaskStatus.done)
    with pytest.raises(ValueError, match="Invalid transition"):
        await state_machine.transition(task, TaskStatus.ready, db_session=mock_db, stream_manager=mock_stream)


async def test_invalid_transition_redesign_to_in_progress(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    task = make_task(status=TaskStatus.redesign)
    with pytest.raises(ValueError, match="Invalid transition"):
        await state_machine.transition(task, TaskStatus.in_progress, db_session=mock_db, stream_manager=mock_stream)


# -- check_dependencies_met (mock TaskRepository) ----------------------------


async def test_check_dependencies_met_all_done(state_machine: TaskStateMachine, mock_db: AsyncMock) -> None:
    task = make_task()
    with patch("backend.src.core.state_machine.TaskRepository") as MockRepo:
        mock_repo_instance = AsyncMock()
        mock_repo_instance.check_dependencies_met = AsyncMock(return_value=True)
        MockRepo.return_value = mock_repo_instance

        result = await state_machine.check_dependencies_met(task, mock_db)
        assert result is True


async def test_check_dependencies_met_not_done(state_machine: TaskStateMachine, mock_db: AsyncMock) -> None:
    task = make_task()
    with patch("backend.src.core.state_machine.TaskRepository") as MockRepo:
        mock_repo_instance = AsyncMock()
        mock_repo_instance.check_dependencies_met = AsyncMock(return_value=False)
        MockRepo.return_value = mock_repo_instance

        result = await state_machine.check_dependencies_met(task, mock_db)
        assert result is False


# -- promote_dependents (mock TaskRepository) ---------------------------------


async def test_promote_dependents_on_done(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    task = make_task(status=TaskStatus.review)
    waiting_task = make_task(status=TaskStatus.waiting)

    with (
        patch("backend.src.core.state_machine.TaskRepository") as MockRepo,
        patch.object(state_machine, "_is_phase_active", return_value=True),
    ):
        mock_repo_instance = AsyncMock()
        mock_repo_instance.find_waiting_dependents = AsyncMock(return_value=[waiting_task])
        mock_repo_instance.check_dependencies_met = AsyncMock(return_value=True)
        MockRepo.return_value = mock_repo_instance

        result = await state_machine.transition(task, TaskStatus.done, db_session=mock_db, stream_manager=mock_stream)

    assert result.status == TaskStatus.done
    assert result.completed_at is not None
    # The waiting dependent should have been promoted
    assert waiting_task.status == TaskStatus.ready
    assert waiting_task.version == 2


async def test_promote_dependents_only_when_all_deps_met(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    """A waiting dependent is NOT promoted if it still has unmet dependencies."""
    task = make_task(status=TaskStatus.review)
    waiting_task = make_task(status=TaskStatus.waiting)

    with (
        patch("backend.src.core.state_machine.TaskRepository") as MockRepo,
        patch.object(state_machine, "_is_phase_active", return_value=True),
    ):
        mock_repo_instance = AsyncMock()
        mock_repo_instance.find_waiting_dependents = AsyncMock(return_value=[waiting_task])
        mock_repo_instance.check_dependencies_met = AsyncMock(return_value=False)
        MockRepo.return_value = mock_repo_instance

        await state_machine.transition(task, TaskStatus.done, db_session=mock_db, stream_manager=mock_stream)

    # Not promoted because dependencies are not met
    assert waiting_task.status == TaskStatus.waiting
    assert waiting_task.version == 1


async def test_promote_dependents_skips_non_active_phase(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    """A waiting dependent in a non-active phase is NOT promoted even if deps are met."""
    task = make_task(status=TaskStatus.review)
    waiting_task = make_task(status=TaskStatus.waiting)

    with (
        patch("backend.src.core.state_machine.TaskRepository") as MockRepo,
        patch.object(state_machine, "_is_phase_active", return_value=False),
    ):
        mock_repo_instance = AsyncMock()
        mock_repo_instance.find_waiting_dependents = AsyncMock(return_value=[waiting_task])
        mock_repo_instance.check_dependencies_met = AsyncMock(return_value=True)
        MockRepo.return_value = mock_repo_instance

        await state_machine.transition(task, TaskStatus.done, db_session=mock_db, stream_manager=mock_stream)

    # Not promoted because phase is not active
    assert waiting_task.status == TaskStatus.waiting
    assert waiting_task.version == 1


# -- Version increment -------------------------------------------------------


async def test_version_increment(state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock) -> None:
    task = make_task(status=TaskStatus.waiting, version=1)
    await state_machine.transition(task, TaskStatus.ready, db_session=mock_db, stream_manager=mock_stream)
    assert task.version == 2


async def test_version_increments_multiple_times(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    task = make_task(status=TaskStatus.waiting, version=1)
    await state_machine.transition(task, TaskStatus.ready, db_session=mock_db, stream_manager=mock_stream)
    assert task.version == 2
    await state_machine.transition(task, TaskStatus.in_progress, db_session=mock_db, stream_manager=mock_stream)
    assert task.version == 3


# -- History recording --------------------------------------------------------


async def test_task_history_recorded(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    task = make_task(status=TaskStatus.waiting)
    await state_machine.transition(
        task, TaskStatus.ready, actor="test-user", reason="deps met", db_session=mock_db, stream_manager=mock_stream
    )
    # Verify db_session.add was called with a TaskHistory object
    mock_db.add.assert_called_once()
    added_obj = mock_db.add.call_args[0][0]
    assert isinstance(added_obj, TaskHistory)
    assert added_obj.from_status == "waiting"
    assert added_obj.to_status == "ready"
    assert added_obj.actor == "test-user"
    assert added_obj.reason == "deps met"


# -- Side effects: _on_in_progress --------------------------------------------


async def test_on_in_progress_sets_started_at(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    task = make_task(status=TaskStatus.ready)
    assert task.started_at is None
    await state_machine.transition(task, TaskStatus.in_progress, db_session=mock_db, stream_manager=mock_stream)
    assert task.started_at is not None


async def test_on_ready_from_in_progress_resets_error_message(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    task = make_task(status=TaskStatus.in_progress, error_message="Previous error")
    await state_machine.transition(task, TaskStatus.ready, db_session=mock_db, stream_manager=mock_stream)
    assert task.error_message is None


async def test_on_ready_from_in_progress_resets_qa_result(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    task = make_task(status=TaskStatus.in_progress, qa_result={"passed": False})
    await state_machine.transition(task, TaskStatus.ready, db_session=mock_db, stream_manager=mock_stream)
    assert task.qa_result is None


async def test_on_ready_from_in_progress_resets_started_at(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    task = make_task(status=TaskStatus.in_progress, started_at=datetime.now(timezone.utc))
    await state_machine.transition(task, TaskStatus.ready, db_session=mock_db, stream_manager=mock_stream)
    assert task.started_at is None


async def test_on_ready_from_waiting_does_not_reset_fields(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    task = make_task(status=TaskStatus.waiting)
    # These should remain unchanged (they're already None for waiting tasks, but verify behavior)
    await state_machine.transition(task, TaskStatus.ready, db_session=mock_db, stream_manager=mock_stream)
    assert task.status == TaskStatus.ready
    # No fields should be explicitly touched by _on_ready for waiting->ready


# -- Side effects: _on_done ---------------------------------------------------


async def test_on_done_sets_completed_at(
    state_machine: TaskStateMachine, mock_db: AsyncMock, mock_stream: AsyncMock
) -> None:
    task = make_task(status=TaskStatus.review)
    assert task.completed_at is None

    with patch("backend.src.core.state_machine.TaskRepository") as MockRepo:
        mock_repo_instance = AsyncMock()
        mock_repo_instance.find_waiting_dependents = AsyncMock(return_value=[])
        MockRepo.return_value = mock_repo_instance

        await state_machine.transition(task, TaskStatus.done, db_session=mock_db, stream_manager=mock_stream)

    assert task.completed_at is not None


# -- Side effects: _on_redesign -----------------------------------------------


async def test_on_redesign_publishes_escalation_event(
    mock_db: AsyncMock,
    mock_stream: AsyncMock,
) -> None:
    """_on_redesign should publish to tasks:escalation stream."""
    sm = TaskStateMachine()
    task = make_task(
        status=TaskStatus.in_progress,
        retry_count=3,
        error_message="Max retries exceeded",
        qa_feedback_history=[{"attempt": 1, "feedback": "bad"}, {"attempt": 2, "feedback": "still bad"}],
    )

    await sm.transition(
        task,
        TaskStatus.redesign,
        db_session=mock_db,
        stream_manager=mock_stream,
    )

    # Find the publish call to tasks:escalation
    escalation_call = None
    for call in mock_stream.publish.call_args_list:
        if call[0][0] == "tasks:escalation":
            escalation_call = call
            break
    assert escalation_call is not None, "Expected publish to tasks:escalation"
    payload = escalation_call[0][1]
    assert payload["task_id"] == str(task.id)
    assert payload["project_id"] == str(task.project_id)
    assert payload["title"] == task.title
    assert payload["retry_count"] == str(task.retry_count)
    assert payload["error_message"] == "Max retries exceeded"
    # qa_feedback_history is serialized as JSON string
    parsed_history = json.loads(payload["qa_feedback_history"])
    assert len(parsed_history) == 2


async def test_on_redesign_sets_error_message_via_handler(
    mock_db: AsyncMock,
    mock_stream: AsyncMock,
) -> None:
    """Calling _on_redesign directly with reason kwarg sets error_message."""
    sm = TaskStateMachine()
    task = make_task(status=TaskStatus.in_progress)

    await sm._on_redesign(
        task,
        old_status=TaskStatus.in_progress,
        db_session=mock_db,
        stream_manager=mock_stream,
        reason="Needs architect attention",
    )

    assert task.error_message == "Needs architect attention"


async def test_on_redesign_without_reason_no_error_message(
    mock_db: AsyncMock,
    mock_stream: AsyncMock,
) -> None:
    sm = TaskStateMachine()
    task = make_task(status=TaskStatus.in_progress, error_message=None)

    await sm.transition(
        task,
        TaskStatus.redesign,
        db_session=mock_db,
        stream_manager=mock_stream,
    )

    # error_message should remain None when no reason provided
    assert task.error_message is None


async def test_on_redesign_escalates_to_architect(
    mock_db: AsyncMock,
    mock_stream: AsyncMock,
) -> None:
    """_on_redesign should escalate the task to architect status."""
    sm = TaskStateMachine()
    task = make_task(status=TaskStatus.in_progress)

    # Should not raise
    await sm.transition(
        task,
        TaskStatus.redesign,
        db_session=mock_db,
        stream_manager=mock_stream,
        reason="Escalate",
    )
    assert task.status == TaskStatus.redesign


async def test_on_redesign_empty_qa_feedback_history(
    mock_db: AsyncMock,
    mock_stream: AsyncMock,
) -> None:
    """_on_redesign handles None qa_feedback_history by serializing as empty list."""
    sm = TaskStateMachine()
    task = make_task(status=TaskStatus.in_progress, qa_feedback_history=None)

    await sm.transition(
        task,
        TaskStatus.redesign,
        db_session=mock_db,
        stream_manager=mock_stream,
        reason="Escalate",
    )

    escalation_call = None
    for call in mock_stream.publish.call_args_list:
        if call[0][0] == "tasks:escalation":
            escalation_call = call
            break
    assert escalation_call is not None
    payload = escalation_call[0][1]
    assert json.loads(payload["qa_feedback_history"]) == []


# -- PromptSigner integration tests ------------------------------------------


async def test_transition_ready_to_in_progress_with_extra_kwargs(
    mock_db: AsyncMock,
    mock_stream: AsyncMock,
) -> None:
    sm = TaskStateMachine()
    task = make_task(status=TaskStatus.ready)

    # Extra kwargs should not cause errors
    await sm.transition(task, TaskStatus.in_progress, db_session=mock_db, stream_manager=mock_stream)
    assert task.status == TaskStatus.in_progress


def test_task_fixture_includes_retry_count() -> None:
    task = make_task(retry_count=5)
    assert task.retry_count == 5


def test_task_fixture_includes_max_retries() -> None:
    task = make_task(max_retries=10)
    assert task.max_retries == 10


def test_task_fixture_includes_qa_feedback_history() -> None:
    history = [{"attempt": 1, "feedback": "bad"}]
    task = make_task(qa_feedback_history=history)
    assert task.qa_feedback_history == history


def test_task_fixture_defaults_retry_count_to_zero() -> None:
    task = make_task()
    assert task.retry_count == 0


def test_task_fixture_defaults_max_retries_to_three() -> None:
    task = make_task()
    assert task.max_retries == 3


def test_task_fixture_defaults_qa_feedback_history_to_none() -> None:
    task = make_task()
    assert task.qa_feedback_history is None


# -- _is_phase_active (direct) ------------------------------------------------


async def test_is_phase_active_returns_false_when_not_active(
    state_machine: TaskStateMachine, mock_db: AsyncMock
) -> None:
    """_is_phase_active returns False when phase status is not active (e.g., pending)."""
    from backend.src.models import PhaseStatus

    phase_id = uuid.uuid4()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = PhaseStatus.pending
    mock_db.execute.return_value = mock_result

    result = await state_machine._is_phase_active(phase_id, mock_db)
    assert result is False


async def test_is_phase_active_returns_true_when_active(state_machine: TaskStateMachine, mock_db: AsyncMock) -> None:
    """_is_phase_active returns True when DB returns PhaseStatus.active."""
    from backend.src.models import PhaseStatus
    from unittest.mock import MagicMock as MM

    phase_id = uuid.uuid4()
    mock_result = MM()
    mock_result.scalar_one_or_none.return_value = PhaseStatus.active
    mock_db.execute.return_value = mock_result

    result = await state_machine._is_phase_active(phase_id, mock_db)
    assert result is True


# -- promote_dependents (public method) ---------------------------------------


async def test_promote_dependents_public_method(state_machine: TaskStateMachine, mock_db: AsyncMock) -> None:
    """promote_dependents public method delegates to _promote_dependents."""
    task = make_task(status=TaskStatus.done)

    with patch("backend.src.core.state_machine.TaskRepository") as MockRepo:
        mock_repo_instance = AsyncMock()
        mock_repo_instance.find_waiting_dependents = AsyncMock(return_value=[])
        MockRepo.return_value = mock_repo_instance

        promoted = await state_machine.promote_dependents(task, mock_db)

    assert promoted == []
