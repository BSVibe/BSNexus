"""Comprehensive tests for PMOrchestrator."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.src.core.orchestrator import PMOrchestrator
from backend.src.core.task_runner import TaskExecutionResult, TaskReviewResult
from backend.src.models import (
    Phase,
    PhaseStatus,
    Task,
    TaskPriority,
    TaskSource,
    TaskStatus,
    TaskType,
)


# ── Helpers ───────────────────────────────────────────────────────────


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
        retry_count=kwargs.get("retry_count", 0),
        max_retries=kwargs.get("max_retries", 3),
        qa_feedback_history=kwargs.get("qa_feedback_history", None),
        started_at=None,
        completed_at=None,
        created_at=now,
        updated_at=now,
    )


def make_phase(
    project_id: uuid.UUID | None = None,
    status: PhaseStatus = PhaseStatus.active,
    order: int = 1,
    **kwargs,
) -> Phase:
    now = datetime.now(timezone.utc)
    return Phase(
        id=kwargs.get("id", uuid.uuid4()),
        project_id=project_id or uuid.uuid4(),
        name=kwargs.get("name", "Phase 1"),
        description=kwargs.get("description", None),
        branch_name=kwargs.get("branch_name", "feature/phase-1"),
        order=order,
        status=status,
        created_at=now,
        updated_at=now,
    )


def _build_orchestrator(
    stream_manager: AsyncMock | None = None,
    task_runner: AsyncMock | None = None,
    state_machine: AsyncMock | None = None,
) -> PMOrchestrator:
    sm = stream_manager or AsyncMock()
    sm.publish = AsyncMock(return_value="mock-msg-id")
    sm.publish_board_event = AsyncMock()
    sm.consume = AsyncMock(return_value=[])
    sm.acknowledge = AsyncMock()
    sm.redis = AsyncMock()
    sm.redis.get = AsyncMock(return_value=None)
    sm.redis.set = AsyncMock()
    sm.redis.incr = AsyncMock()
    sm.redis.expire = AsyncMock()
    return PMOrchestrator(
        stream_manager=sm,
        task_runner=task_runner or AsyncMock(),
        state_machine=state_machine or AsyncMock(),
    )


def _mock_db() -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    return db


def _mock_db_session_factory(db: AsyncMock):
    @asynccontextmanager
    async def factory():
        yield db

    return factory


# ── Test: execution loop picks ready task and executes ─────────────


@pytest.mark.asyncio
async def test_execution_loop_picks_ready_task_and_executes() -> None:
    """When a ready task exists and no active tasks, the orchestrator executes it."""
    project_id = uuid.uuid4()
    task = make_task(status=TaskStatus.ready, project_id=project_id)

    orch = _build_orchestrator()
    db = _mock_db()
    db_factory = _mock_db_session_factory(db)

    mock_repo = AsyncMock()
    mock_repo.count_active_tasks = AsyncMock(return_value=0)
    mock_repo.list_ready_by_priority = AsyncMock(return_value=[task])

    mock_phase_repo = AsyncMock()
    mock_phase_repo.get_active_phase = AsyncMock(return_value=None)
    mock_phase_repo.get_first_pending_phase = AsyncMock(return_value=None)

    mock_project_repo = AsyncMock()
    mock_project = MagicMock()
    mock_project.repo_path = "/tmp/repo"
    mock_project_repo.get_by_id = AsyncMock(return_value=mock_project)

    async def stop_after_execute(*args, **kwargs):
        orch._running = False

    with (
        patch("backend.src.core.orchestrator.TaskRepository", return_value=mock_repo),
        patch("backend.src.core.orchestrator.PhaseRepository", return_value=mock_phase_repo),
        patch("backend.src.core.orchestrator.ProjectRepository", return_value=mock_project_repo),
        patch.object(orch, "_execute_and_review", new_callable=AsyncMock, side_effect=stop_after_execute) as mock_exec,
    ):
        orch._running = True
        await orch._execution_loop(project_id, db_factory)

    mock_exec.assert_awaited_once()
    # Verify the task was transitioned to in_progress
    orch.state_machine.transition.assert_awaited_once()
    call_kwargs = orch.state_machine.transition.call_args.kwargs
    assert call_kwargs["new_status"] == TaskStatus.in_progress


# ── Test: execution loop transitions to done on QA pass ────────────


@pytest.mark.asyncio
async def test_execution_loop_transitions_to_done_on_qa_pass() -> None:
    """Full happy path via _execute_and_review: execute succeeds, review passes, task transitions to done."""
    project_id = uuid.uuid4()
    task = make_task(status=TaskStatus.in_progress, project_id=project_id)

    mock_task_runner = AsyncMock()
    mock_task_runner.execute_task = AsyncMock(
        return_value=TaskExecutionResult(success=True)
    )
    mock_task_runner.review_task = AsyncMock(
        return_value=TaskReviewResult(passed=True, commit_hash="abc123")
    )

    orch = _build_orchestrator(task_runner=mock_task_runner)
    db = _mock_db()
    db_factory = _mock_db_session_factory(db)

    mock_repo = AsyncMock()
    mock_repo.get_by_id = AsyncMock(return_value=task)

    with patch("backend.src.core.orchestrator.TaskRepository", return_value=mock_repo):
        await orch._execute_and_review(task, "/tmp/repo", project_id, db_factory)

    # Should transition: in_progress->review, then review->done
    assert orch.state_machine.transition.await_count == 2
    done_calls = [
        c for c in orch.state_machine.transition.call_args_list
        if c.kwargs.get("new_status") == TaskStatus.done
    ]
    assert len(done_calls) == 1
    assert task.commit_hash == "abc123"


# ── Test: execution loop skips when active task exists ─────────────


@pytest.mark.asyncio
async def test_execution_loop_skips_when_active_task_exists() -> None:
    """When an active task exists, no new task is picked up."""
    project_id = uuid.uuid4()

    orch = _build_orchestrator()
    db = _mock_db()
    db_factory = _mock_db_session_factory(db)

    mock_repo = AsyncMock()
    mock_repo.count_active_tasks = AsyncMock(return_value=1)

    async def stop_loop(_):
        orch._running = False

    with patch("backend.src.core.orchestrator.TaskRepository", return_value=mock_repo):
        with patch("backend.src.core.orchestrator.asyncio.sleep", side_effect=stop_loop):
            orch._running = True
            await orch._execution_loop(project_id, db_factory)

    # No execution should happen
    orch.task_runner.execute_task.assert_not_awaited()


# ── Test: handle execution failure retries under limit ─────────────


@pytest.mark.asyncio
async def test_handle_execution_failure_retries_under_limit() -> None:
    """When retry_count < max_retries, task transitions back to ready."""
    task = make_task(status=TaskStatus.in_progress, retry_count=0, max_retries=3)
    orch = _build_orchestrator()
    db = _mock_db()

    await orch._handle_execution_failure(task, db, "Some error", "runtime")

    assert task.retry_count == 1
    # Should transition to ready (retry)
    orch.state_machine.transition.assert_awaited_once()
    call_kwargs = orch.state_machine.transition.call_args.kwargs
    assert call_kwargs["new_status"] == TaskStatus.ready
    assert task.qa_feedback_history is not None
    assert len(task.qa_feedback_history) == 1
    assert task.qa_feedback_history[0]["type"] == "execution_failure"


# ── Test: handle execution failure escalates at limit ──────────────


@pytest.mark.asyncio
async def test_handle_execution_failure_escalates_at_limit() -> None:
    """When retry_count > max_retries after increment, task transitions to redesign and bug task is created."""
    task = make_task(status=TaskStatus.in_progress, retry_count=3, max_retries=3)
    orch = _build_orchestrator()
    db = _mock_db()

    with patch.object(orch, "_create_bug_task", new_callable=AsyncMock) as mock_create_bug:
        await orch._handle_execution_failure(task, db, "Fatal error", "runtime")

    assert task.retry_count == 4
    call_kwargs = orch.state_machine.transition.call_args.kwargs
    assert call_kwargs["new_status"] == TaskStatus.redesign
    mock_create_bug.assert_awaited_once()


# ── Test: handle QA failure retries ────────────────────────────────


@pytest.mark.asyncio
async def test_handle_qa_failure_retries() -> None:
    """QA fails, under limit, task goes back to ready."""
    task = make_task(status=TaskStatus.review, retry_count=0, max_retries=3)
    orch = _build_orchestrator()
    db = _mock_db()

    await orch._handle_qa_failure(task, db, feedback="Tests failing")

    assert task.retry_count == 1
    call_kwargs = orch.state_machine.transition.call_args.kwargs
    assert call_kwargs["new_status"] == TaskStatus.ready
    assert task.qa_feedback_history[0]["type"] == "qa_failure"
    assert task.qa_feedback_history[0]["feedback"] == "Tests failing"


# ── Test: handle QA failure escalates ──────────────────────────────


@pytest.mark.asyncio
async def test_handle_qa_failure_escalates() -> None:
    """QA fails past max retries, escalates to redesign."""
    task = make_task(status=TaskStatus.review, retry_count=3, max_retries=3)
    orch = _build_orchestrator()
    db = _mock_db()

    with patch.object(orch, "_create_bug_task", new_callable=AsyncMock) as mock_create_bug:
        await orch._handle_qa_failure(task, db, feedback="Still broken")

    assert task.retry_count == 4
    call_kwargs = orch.state_machine.transition.call_args.kwargs
    assert call_kwargs["new_status"] == TaskStatus.redesign
    mock_create_bug.assert_awaited_once()


# ── Test: promote waiting tasks transitions met deps ───────────────


@pytest.mark.asyncio
async def test_promote_waiting_tasks_transitions_met_deps() -> None:
    """Waiting tasks with met dependencies transition to ready."""
    project_id = uuid.uuid4()
    phase = make_phase(project_id=project_id)
    task = make_task(status=TaskStatus.waiting, project_id=project_id, phase_id=phase.id)

    orch = _build_orchestrator()
    db = _mock_db()
    db_factory = _mock_db_session_factory(db)

    mock_phase_repo = AsyncMock()
    mock_phase_repo.get_active_phase = AsyncMock(return_value=phase)

    mock_task_repo = AsyncMock()
    mock_task_repo.list_waiting_in_phase = AsyncMock(return_value=[task])
    mock_task_repo.check_dependencies_met = AsyncMock(return_value=True)

    with (
        patch("backend.src.core.orchestrator.PhaseRepository", return_value=mock_phase_repo),
        patch("backend.src.core.orchestrator.TaskRepository", return_value=mock_task_repo),
    ):
        await orch._promote_waiting_tasks(project_id, db_factory)

    orch.state_machine.transition.assert_awaited_once()
    call_kwargs = orch.state_machine.transition.call_args.kwargs
    assert call_kwargs["new_status"] == TaskStatus.ready
    assert call_kwargs["reason"] == "All dependencies met"


# ── Test: promote waiting tasks skips unmet deps ───────────────────


@pytest.mark.asyncio
async def test_promote_waiting_tasks_skips_unmet_deps() -> None:
    """Waiting tasks with unmet dependencies are not promoted."""
    project_id = uuid.uuid4()
    phase = make_phase(project_id=project_id)
    task = make_task(status=TaskStatus.waiting, project_id=project_id, phase_id=phase.id)

    orch = _build_orchestrator()
    db = _mock_db()
    db_factory = _mock_db_session_factory(db)

    mock_phase_repo = AsyncMock()
    mock_phase_repo.get_active_phase = AsyncMock(return_value=phase)

    mock_task_repo = AsyncMock()
    mock_task_repo.list_waiting_in_phase = AsyncMock(return_value=[task])
    mock_task_repo.check_dependencies_met = AsyncMock(return_value=False)

    with (
        patch("backend.src.core.orchestrator.PhaseRepository", return_value=mock_phase_repo),
        patch("backend.src.core.orchestrator.TaskRepository", return_value=mock_task_repo),
    ):
        await orch._promote_waiting_tasks(project_id, db_factory)

    orch.state_machine.transition.assert_not_awaited()


# ── Test: check and advance phase completes and advances ───────────


@pytest.mark.asyncio
async def test_check_and_advance_phase_completes_and_advances() -> None:
    """When all tasks done, active phase completes and next phase activates."""
    project_id = uuid.uuid4()
    active_phase = make_phase(project_id=project_id, status=PhaseStatus.active, order=1, name="Phase 1")
    next_phase = make_phase(project_id=project_id, status=PhaseStatus.pending, order=2, name="Phase 2")

    orch = _build_orchestrator()
    db = _mock_db()

    mock_phase_repo = AsyncMock()
    mock_phase_repo.get_active_phase = AsyncMock(return_value=active_phase)
    mock_phase_repo.count_incomplete_tasks = AsyncMock(return_value=0)
    mock_phase_repo.get_next_pending_phase = AsyncMock(return_value=next_phase)

    with patch("backend.src.core.orchestrator.PhaseRepository", return_value=mock_phase_repo):
        events = await orch._check_and_advance_phase(project_id, db)

    assert active_phase.status == PhaseStatus.completed
    assert next_phase.status == PhaseStatus.active
    assert len(events) == 2
    assert events[0][0] == "phase_completed"
    assert events[1][0] == "phase_activated"


# ── Test: check and advance phase no action when incomplete ────────


@pytest.mark.asyncio
async def test_check_and_advance_phase_no_action_when_incomplete() -> None:
    """Incomplete tasks mean no phase advancement."""
    project_id = uuid.uuid4()
    active_phase = make_phase(project_id=project_id, status=PhaseStatus.active)

    orch = _build_orchestrator()
    db = _mock_db()

    mock_phase_repo = AsyncMock()
    mock_phase_repo.get_active_phase = AsyncMock(return_value=active_phase)
    mock_phase_repo.count_incomplete_tasks = AsyncMock(return_value=3)

    with patch("backend.src.core.orchestrator.PhaseRepository", return_value=mock_phase_repo):
        events = await orch._check_and_advance_phase(project_id, db)

    assert events == []
    assert active_phase.status == PhaseStatus.active


# ── Test: escalation loop processes and acknowledges ───────────────


@pytest.mark.asyncio
async def test_escalation_loop_processes_and_acknowledges() -> None:
    """Escalation loop consumes a message, processes it, and ACKs."""
    project_id = uuid.uuid4()
    task_id = uuid.uuid4()

    msg = {
        "task_id": str(task_id),
        "project_id": str(project_id),
        "_message_id": "msg-1",
        "title": "Broken task",
        "retry_count": "3",
        "qa_feedback_history": "[]",
        "error_message": "fail",
    }

    orch = _build_orchestrator()
    db = _mock_db()
    db_factory = _mock_db_session_factory(db)

    call_count = 0

    async def mock_consume(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return [msg]
        orch._running = False
        return []

    orch.stream_manager.consume = AsyncMock(side_effect=mock_consume)

    with patch.object(orch, "_process_escalation", new_callable=AsyncMock) as mock_process:
        orch._running = True
        await orch._escalation_loop(project_id, db_factory)

    mock_process.assert_awaited_once()
    orch.stream_manager.acknowledge.assert_awaited()


# ── Test: escalation loop skips other project ──────────────────────


@pytest.mark.asyncio
async def test_escalation_loop_skips_other_project() -> None:
    """Messages for a different project_id are ACKed and skipped."""
    project_id = uuid.uuid4()
    other_project_id = uuid.uuid4()

    msg = {
        "task_id": str(uuid.uuid4()),
        "project_id": str(other_project_id),
        "_message_id": "msg-2",
    }

    orch = _build_orchestrator()
    db = _mock_db()
    db_factory = _mock_db_session_factory(db)

    call_count = 0

    async def mock_consume(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return [msg]
        orch._running = False
        return []

    orch.stream_manager.consume = AsyncMock(side_effect=mock_consume)

    with patch.object(orch, "_process_escalation", new_callable=AsyncMock) as mock_process:
        orch._running = True
        await orch._escalation_loop(project_id, db_factory)

    # Should NOT process, but should ACK
    mock_process.assert_not_awaited()
    orch.stream_manager.acknowledge.assert_awaited()


# ── Test: mark redesign needs intervention ─────────────────────────


@pytest.mark.asyncio
async def test_mark_redesign_needs_intervention() -> None:
    """Sets error_message, publishes event, sets Redis key."""
    task = make_task(status=TaskStatus.redesign)
    orch = _build_orchestrator()
    db = _mock_db()

    await orch._mark_redesign_needs_intervention(task, "LLM error: timeout", db)

    assert task.error_message == "Redesign failed: LLM error: timeout"
    orch.stream_manager.redis.set.assert_awaited_once()
    set_args = orch.stream_manager.redis.set.call_args
    assert set_args[0][0] == f"task:{task.id}:needs_intervention"
    assert set_args[0][1] == "1"

    orch.stream_manager.publish_board_event.assert_awaited_once()
    event_type = orch.stream_manager.publish_board_event.call_args[0][0]
    assert event_type == "auto_redesign_failed"


# ── Test: execute and review handles missing task ──────────────────


@pytest.mark.asyncio
async def test_execute_and_review_handles_missing_task() -> None:
    """If task disappears during execution, method returns gracefully."""
    project_id = uuid.uuid4()
    task = make_task(status=TaskStatus.in_progress, project_id=project_id)

    mock_task_runner = AsyncMock()
    mock_task_runner.execute_task = AsyncMock(
        return_value=TaskExecutionResult(success=True)
    )

    orch = _build_orchestrator(task_runner=mock_task_runner)
    db = _mock_db()
    db_factory = _mock_db_session_factory(db)

    mock_repo = AsyncMock()
    mock_repo.get_by_id = AsyncMock(return_value=None)  # Task disappeared

    with patch("backend.src.core.orchestrator.TaskRepository", return_value=mock_repo):
        await orch._execute_and_review(task, "/tmp/repo", project_id, db_factory)

    # No transition should happen since task was not found
    orch.state_machine.transition.assert_not_awaited()


# ── Test: recover orphaned redesign tasks republishes ──────────────


@pytest.mark.asyncio
async def test_recover_orphaned_redesign_tasks_republishes() -> None:
    """Stuck redesign task without intervention flag gets re-published."""
    project_id = uuid.uuid4()
    task = make_task(
        status=TaskStatus.redesign,
        project_id=project_id,
        title="Stuck task",
        retry_count=3,
    )

    orch = _build_orchestrator()
    db = _mock_db()
    db_factory = _mock_db_session_factory(db)

    mock_repo = AsyncMock()
    mock_repo.list_by_project = AsyncMock(return_value=[task])

    # No intervention key, no recovery count
    orch.stream_manager.redis.get = AsyncMock(return_value=None)

    with patch("backend.src.core.orchestrator.TaskRepository", return_value=mock_repo):
        await orch._recover_orphaned_redesign_tasks(project_id, db_factory)

    # Should publish to escalation stream
    orch.stream_manager.publish.assert_awaited_once()
    publish_args = orch.stream_manager.publish.call_args
    assert publish_args[0][0] == "tasks:escalation"
    assert publish_args[0][1]["task_id"] == str(task.id)

    # Should increment recovery count
    orch.stream_manager.redis.set.assert_awaited()


# ── Test: recover orphaned redesign tasks skips intervention ───────


@pytest.mark.asyncio
async def test_recover_orphaned_redesign_tasks_skips_intervention() -> None:
    """Already flagged for intervention is skipped."""
    project_id = uuid.uuid4()
    task = make_task(status=TaskStatus.redesign, project_id=project_id)

    orch = _build_orchestrator()
    db = _mock_db()
    db_factory = _mock_db_session_factory(db)

    mock_repo = AsyncMock()
    mock_repo.list_by_project = AsyncMock(return_value=[task])

    # Intervention key exists
    orch.stream_manager.redis.get = AsyncMock(return_value="1")

    with patch("backend.src.core.orchestrator.TaskRepository", return_value=mock_repo):
        await orch._recover_orphaned_redesign_tasks(project_id, db_factory)

    # Should NOT publish
    orch.stream_manager.publish.assert_not_awaited()


# ── Test: promote activates first pending phase if no active ───────


@pytest.mark.asyncio
async def test_promote_waiting_tasks_activates_first_pending_phase() -> None:
    """If no active phase exists, the first pending phase is activated."""
    project_id = uuid.uuid4()
    pending_phase = make_phase(project_id=project_id, status=PhaseStatus.pending)
    task = make_task(status=TaskStatus.waiting, project_id=project_id, phase_id=pending_phase.id)

    orch = _build_orchestrator()
    db = _mock_db()
    db_factory = _mock_db_session_factory(db)

    mock_phase_repo = AsyncMock()
    mock_phase_repo.get_active_phase = AsyncMock(return_value=None)
    mock_phase_repo.get_first_pending_phase = AsyncMock(return_value=pending_phase)

    mock_task_repo = AsyncMock()
    mock_task_repo.list_waiting_in_phase = AsyncMock(return_value=[task])
    mock_task_repo.check_dependencies_met = AsyncMock(return_value=True)

    with (
        patch("backend.src.core.orchestrator.PhaseRepository", return_value=mock_phase_repo),
        patch("backend.src.core.orchestrator.TaskRepository", return_value=mock_task_repo),
    ):
        await orch._promote_waiting_tasks(project_id, db_factory)

    assert pending_phase.status == PhaseStatus.active
    orch.state_machine.transition.assert_awaited_once()


# ── Test: execute_and_review handles execution failure ─────────────


@pytest.mark.asyncio
async def test_execute_and_review_handles_execution_failure() -> None:
    """When execution fails, _handle_execution_failure is called."""
    project_id = uuid.uuid4()
    task = make_task(status=TaskStatus.in_progress, project_id=project_id)

    mock_task_runner = AsyncMock()
    mock_task_runner.execute_task = AsyncMock(
        return_value=TaskExecutionResult(success=False, error_message="compile error", error_category="runtime")
    )

    orch = _build_orchestrator(task_runner=mock_task_runner)
    db = _mock_db()
    db_factory = _mock_db_session_factory(db)

    mock_repo = AsyncMock()
    mock_repo.get_by_id = AsyncMock(return_value=task)

    with patch("backend.src.core.orchestrator.TaskRepository", return_value=mock_repo):
        with patch.object(orch, "_handle_execution_failure", new_callable=AsyncMock) as mock_handle:
            await orch._execute_and_review(task, "/tmp/repo", project_id, db_factory)

    mock_handle.assert_awaited_once_with(task, db, "compile error", "runtime")


# ── Test: execute_and_review handles QA failure ────────────────────


@pytest.mark.asyncio
async def test_execute_and_review_handles_qa_failure() -> None:
    """When execution succeeds but QA fails, _handle_qa_failure is called."""
    project_id = uuid.uuid4()
    task = make_task(status=TaskStatus.in_progress, project_id=project_id)

    mock_task_runner = AsyncMock()
    mock_task_runner.execute_task = AsyncMock(
        return_value=TaskExecutionResult(success=True)
    )
    mock_task_runner.review_task = AsyncMock(
        return_value=TaskReviewResult(
            passed=False, feedback="Tests failing", error_message="assertion error", error_category="test"
        )
    )

    orch = _build_orchestrator(task_runner=mock_task_runner)
    db = _mock_db()
    db_factory = _mock_db_session_factory(db)

    mock_repo = AsyncMock()
    mock_repo.get_by_id = AsyncMock(return_value=task)

    with patch("backend.src.core.orchestrator.TaskRepository", return_value=mock_repo):
        with patch.object(orch, "_handle_qa_failure", new_callable=AsyncMock) as mock_handle:
            await orch._execute_and_review(task, "/tmp/repo", project_id, db_factory)

    mock_handle.assert_awaited_once()


# ── Test: check_and_advance_phase returns empty when no active phase


@pytest.mark.asyncio
async def test_check_and_advance_phase_returns_empty_when_no_active() -> None:
    """When there is no active phase, returns empty events list."""
    project_id = uuid.uuid4()
    orch = _build_orchestrator()
    db = _mock_db()

    mock_phase_repo = AsyncMock()
    mock_phase_repo.get_active_phase = AsyncMock(return_value=None)

    with patch("backend.src.core.orchestrator.PhaseRepository", return_value=mock_phase_repo):
        events = await orch._check_and_advance_phase(project_id, db)

    assert events == []


# ── Test: recover skips redesign-failed DB marker tasks ────────────


@pytest.mark.asyncio
async def test_recover_skips_redesign_failed_db_marker() -> None:
    """Tasks with 'Redesign failed:' error_message are skipped and flagged."""
    project_id = uuid.uuid4()
    task = make_task(
        status=TaskStatus.redesign,
        project_id=project_id,
        error_message="Redesign failed: LLM error",
    )

    orch = _build_orchestrator()
    db = _mock_db()
    db_factory = _mock_db_session_factory(db)

    mock_repo = AsyncMock()
    mock_repo.list_by_project = AsyncMock(return_value=[task])

    # No intervention key initially
    orch.stream_manager.redis.get = AsyncMock(return_value=None)

    with patch("backend.src.core.orchestrator.TaskRepository", return_value=mock_repo):
        await orch._recover_orphaned_redesign_tasks(project_id, db_factory)

    # Should NOT publish to escalation
    orch.stream_manager.publish.assert_not_awaited()
    # Should set intervention key
    orch.stream_manager.redis.set.assert_awaited()


# ── Test: recover flags task after max recovery count ──────────────


@pytest.mark.asyncio
async def test_recover_flags_after_max_recovery_count() -> None:
    """Task recovered 3+ times without resolution gets flagged for intervention."""
    project_id = uuid.uuid4()
    task = make_task(status=TaskStatus.redesign, project_id=project_id)

    orch = _build_orchestrator()
    db = _mock_db()
    db_factory = _mock_db_session_factory(db)

    mock_repo = AsyncMock()
    mock_repo.list_by_project = AsyncMock(return_value=[task])

    call_count = 0

    async def mock_get(key):
        nonlocal call_count
        call_count += 1
        # First call is intervention key check -> None
        # Second call is recovery_count -> "3"
        if call_count == 1:
            return None
        return "3"

    orch.stream_manager.redis.get = AsyncMock(side_effect=mock_get)

    with patch("backend.src.core.orchestrator.TaskRepository", return_value=mock_repo):
        await orch._recover_orphaned_redesign_tasks(project_id, db_factory)

    # Should NOT publish (flagged for intervention instead)
    orch.stream_manager.publish.assert_not_awaited()
    # Should set intervention key
    orch.stream_manager.redis.set.assert_awaited()


# ── Test: queue_next returns task when ready ───────────────────────


@pytest.mark.asyncio
async def test_queue_next_returns_task_when_ready() -> None:
    """queue_next returns the first ready task without transitioning it."""
    project_id = uuid.uuid4()
    task = make_task(status=TaskStatus.ready, project_id=project_id)

    orch = _build_orchestrator()
    db = _mock_db()

    mock_repo = AsyncMock()
    mock_repo.count_active_tasks = AsyncMock(return_value=0)
    mock_repo.list_ready_by_priority = AsyncMock(return_value=[task])

    with patch("backend.src.core.orchestrator.TaskRepository", return_value=mock_repo):
        result = await orch.queue_next(project_id, db)

    assert result is task
    # queue_next no longer transitions — the execution loop handles that
    orch.state_machine.transition.assert_not_called()


# ── Test: queue_next returns None when active task exists ──────────


@pytest.mark.asyncio
async def test_queue_next_returns_none_when_active() -> None:
    """queue_next returns None if there is already an active task."""
    project_id = uuid.uuid4()
    orch = _build_orchestrator()
    db = _mock_db()

    mock_repo = AsyncMock()
    mock_repo.count_active_tasks = AsyncMock(return_value=1)

    with patch("backend.src.core.orchestrator.TaskRepository", return_value=mock_repo):
        result = await orch.queue_next(project_id, db)

    assert result is None


# ── Test: _process_escalation skips on invalid UUID ─────────────────


@pytest.mark.asyncio
async def test_process_escalation_skips_invalid_uuid() -> None:
    """If task_id is not a valid UUID, _process_escalation returns early."""
    orch = _build_orchestrator()
    db = _mock_db()

    await orch._process_escalation({"task_id": "not-a-valid-uuid"}, db)

    orch.state_machine.transition.assert_not_awaited()


# ── Test: _process_escalation skips if task not found ──────────────


@pytest.mark.asyncio
async def test_process_escalation_skips_if_task_not_found() -> None:
    """If task doesn't exist, _process_escalation returns early."""
    orch = _build_orchestrator()
    db = _mock_db()

    mock_repo = AsyncMock()
    mock_repo.get_by_id = AsyncMock(return_value=None)

    with patch("backend.src.core.orchestrator.TaskRepository", return_value=mock_repo):
        await orch._process_escalation({"task_id": str(uuid.uuid4())}, db)

    orch.state_machine.transition.assert_not_awaited()


# ── Test: _process_escalation skips if not in redesign status ──────


@pytest.mark.asyncio
async def test_process_escalation_skips_if_not_redesign() -> None:
    """If task is not in redesign status, it is skipped."""
    task = make_task(status=TaskStatus.ready)
    orch = _build_orchestrator()
    db = _mock_db()

    mock_repo = AsyncMock()
    mock_repo.get_by_id = AsyncMock(return_value=task)

    with patch("backend.src.core.orchestrator.TaskRepository", return_value=mock_repo):
        await orch._process_escalation({"task_id": str(task.id)}, db)

    orch.state_machine.transition.assert_not_awaited()


# ── Test: _process_escalation handles environment error ────────────


@pytest.mark.asyncio
async def test_process_escalation_handles_environment_error() -> None:
    """Environment errors trigger intervention, not redesign."""
    task = make_task(
        status=TaskStatus.redesign,
        qa_feedback_history=[{"error_category": "environment"}],
        error_message="Permission denied",
    )
    orch = _build_orchestrator()
    db = _mock_db()

    mock_repo = AsyncMock()
    mock_repo.get_by_id = AsyncMock(return_value=task)

    with patch("backend.src.core.orchestrator.TaskRepository", return_value=mock_repo):
        with patch.object(orch, "_mark_redesign_needs_intervention", new_callable=AsyncMock) as mock_intervene:
            await orch._process_escalation({"task_id": str(task.id)}, db)

    mock_intervene.assert_awaited_once()
    assert "Environment error" in mock_intervene.call_args[0][1]


# ── Test: _process_escalation max auto-redesigns triggers intervention


@pytest.mark.asyncio
async def test_process_escalation_max_auto_redesigns_triggers_intervention() -> None:
    """When auto-redesign limit is reached, task is flagged for intervention."""
    task = make_task(status=TaskStatus.redesign, qa_feedback_history=[])
    orch = _build_orchestrator()
    db = _mock_db()

    mock_repo = AsyncMock()
    mock_repo.get_by_id = AsyncMock(return_value=task)

    # Redis returns high count
    orch.stream_manager.redis.get = AsyncMock(return_value="100")

    with (
        patch("backend.src.core.orchestrator.TaskRepository", return_value=mock_repo),
        patch("backend.src.core.orchestrator.settings") as mock_settings,
        patch.object(orch, "_mark_redesign_needs_intervention", new_callable=AsyncMock) as mock_intervene,
    ):
        mock_settings.max_auto_redesigns = 3
        await orch._process_escalation({"task_id": str(task.id)}, db)

    mock_intervene.assert_awaited_once()
    assert "Auto-redesign limit" in mock_intervene.call_args[0][1]


# ── Test: _process_escalation project not found ────────────────────


@pytest.mark.asyncio
async def test_process_escalation_project_not_found() -> None:
    """When project is not found, task is flagged for intervention."""
    task = make_task(status=TaskStatus.redesign, qa_feedback_history=[])
    orch = _build_orchestrator()
    db = _mock_db()

    mock_task_repo = AsyncMock()
    mock_task_repo.get_by_id = AsyncMock(return_value=task)

    mock_project_repo = AsyncMock()
    mock_project_repo.get_by_id = AsyncMock(return_value=None)

    orch.stream_manager.redis.get = AsyncMock(return_value=None)

    with (
        patch("backend.src.core.orchestrator.TaskRepository", return_value=mock_task_repo),
        patch("backend.src.core.orchestrator.ProjectRepository", return_value=mock_project_repo),
        patch("backend.src.core.orchestrator.settings") as mock_settings,
        patch.object(orch, "_mark_redesign_needs_intervention", new_callable=AsyncMock) as mock_intervene,
    ):
        mock_settings.max_auto_redesigns = 10
        await orch._process_escalation({"task_id": str(task.id)}, db)

    mock_intervene.assert_awaited_once()
    assert "Project not found" in mock_intervene.call_args[0][1]


# ── Test: _process_escalation no LLM config ───────────────────────


@pytest.mark.asyncio
async def test_process_escalation_no_llm_config() -> None:
    """When LLM config is missing, task is flagged for intervention."""
    task = make_task(status=TaskStatus.redesign, qa_feedback_history=[])
    orch = _build_orchestrator()
    db = _mock_db()

    mock_task_repo = AsyncMock()
    mock_task_repo.get_by_id = AsyncMock(return_value=task)

    mock_project = MagicMock()
    mock_project.id = uuid.uuid4()
    mock_project_repo = AsyncMock()
    mock_project_repo.get_by_id = AsyncMock(return_value=mock_project)

    orch.stream_manager.redis.get = AsyncMock(return_value=None)

    with (
        patch("backend.src.core.orchestrator.TaskRepository", return_value=mock_task_repo),
        patch("backend.src.core.orchestrator.ProjectRepository", return_value=mock_project_repo),
        patch("backend.src.core.orchestrator.settings") as mock_settings,
        patch(
            "backend.src.core.orchestrator.create_llm_client_from_project",
            side_effect=ValueError("No LLM config"),
        ),
        patch.object(orch, "_mark_redesign_needs_intervention", new_callable=AsyncMock) as mock_intervene,
    ):
        mock_settings.max_auto_redesigns = 10
        await orch._process_escalation({"task_id": str(task.id)}, db)

    mock_intervene.assert_awaited_once()
    assert "No architect LLM configuration" in mock_intervene.call_args[0][1]


# ── Test: _process_escalation phase not found ──────────────────────


@pytest.mark.asyncio
async def test_process_escalation_phase_not_found() -> None:
    """When phase is not found, task is flagged for intervention."""
    task = make_task(status=TaskStatus.redesign, qa_feedback_history=[])
    orch = _build_orchestrator()
    db = _mock_db()

    mock_task_repo = AsyncMock()
    mock_task_repo.get_by_id = AsyncMock(return_value=task)

    mock_project = MagicMock()
    mock_project.id = uuid.uuid4()
    mock_project_repo = AsyncMock()
    mock_project_repo.get_by_id = AsyncMock(return_value=mock_project)

    mock_phase_repo = AsyncMock()
    mock_phase_repo.get_by_id = AsyncMock(return_value=None)

    mock_llm_client = AsyncMock()

    orch.stream_manager.redis.get = AsyncMock(return_value=None)

    with (
        patch("backend.src.core.orchestrator.TaskRepository", return_value=mock_task_repo),
        patch("backend.src.core.orchestrator.ProjectRepository", return_value=mock_project_repo),
        patch("backend.src.core.orchestrator.PhaseRepository", return_value=mock_phase_repo),
        patch("backend.src.core.orchestrator.settings") as mock_settings,
        patch("backend.src.core.orchestrator.create_llm_client_from_project", return_value=mock_llm_client),
        patch.object(orch, "_mark_redesign_needs_intervention", new_callable=AsyncMock) as mock_intervene,
    ):
        mock_settings.max_auto_redesigns = 10
        await orch._process_escalation({"task_id": str(task.id)}, db)

    mock_intervene.assert_awaited_once()
    assert "Phase not found" in mock_intervene.call_args[0][1]


# ── Test: _process_escalation LLM error triggers intervention ──────


@pytest.mark.asyncio
async def test_process_escalation_llm_error_triggers_intervention() -> None:
    """When LLM call fails, task is flagged for intervention."""
    from backend.src.core.llm_client import LLMError

    task = make_task(status=TaskStatus.redesign, qa_feedback_history=[])
    phase = make_phase(project_id=task.project_id)
    task.phase_id = phase.id
    orch = _build_orchestrator()
    db = _mock_db()

    mock_task_repo = AsyncMock()
    mock_task_repo.get_by_id = AsyncMock(return_value=task)
    mock_task_repo.list_incomplete_in_phase = AsyncMock(return_value=[])
    mock_task_repo.list_done_in_phase = AsyncMock(return_value=[])

    mock_project = MagicMock()
    mock_project.id = task.project_id
    mock_project_repo = AsyncMock()
    mock_project_repo.get_by_id = AsyncMock(return_value=mock_project)

    mock_phase_repo = AsyncMock()
    mock_phase_repo.get_by_id = AsyncMock(return_value=phase)

    mock_llm_client = AsyncMock()
    mock_llm_client.structured_output = AsyncMock(side_effect=LLMError("Timeout"))

    orch.stream_manager.redis.get = AsyncMock(return_value=None)

    with (
        patch("backend.src.core.orchestrator.TaskRepository", return_value=mock_task_repo),
        patch("backend.src.core.orchestrator.ProjectRepository", return_value=mock_project_repo),
        patch("backend.src.core.orchestrator.PhaseRepository", return_value=mock_phase_repo),
        patch("backend.src.core.orchestrator.settings") as mock_settings,
        patch("backend.src.core.orchestrator.create_llm_client_from_project", return_value=mock_llm_client),
        patch("backend.src.core.orchestrator.get_prompt", return_value="mock prompt {failed_task_title} {failed_task_error} {failed_task_history} {done_tasks} {incomplete_tasks} {phase_name} {branch_name}"),
        patch.object(orch, "_mark_redesign_needs_intervention", new_callable=AsyncMock) as mock_intervene,
    ):
        mock_settings.max_auto_redesigns = 10
        await orch._process_escalation({"task_id": str(task.id)}, db)

    mock_intervene.assert_awaited_once()
    assert "LLM error" in mock_intervene.call_args[0][1]


# ── Test: _process_escalation invalid tasks format ─────────────────


@pytest.mark.asyncio
async def test_process_escalation_invalid_tasks_format() -> None:
    """When LLM returns invalid tasks format, task is flagged for intervention."""
    task = make_task(status=TaskStatus.redesign, qa_feedback_history=[])
    phase = make_phase(project_id=task.project_id)
    task.phase_id = phase.id
    orch = _build_orchestrator()
    db = _mock_db()

    mock_task_repo = AsyncMock()
    mock_task_repo.get_by_id = AsyncMock(return_value=task)
    mock_task_repo.list_incomplete_in_phase = AsyncMock(return_value=[])
    mock_task_repo.list_done_in_phase = AsyncMock(return_value=[])

    mock_project = MagicMock()
    mock_project.id = task.project_id
    mock_project_repo = AsyncMock()
    mock_project_repo.get_by_id = AsyncMock(return_value=mock_project)

    mock_phase_repo = AsyncMock()
    mock_phase_repo.get_by_id = AsyncMock(return_value=phase)

    mock_llm_client = AsyncMock()
    mock_llm_client.structured_output = AsyncMock(return_value={"reasoning": "test", "tasks": "not a list"})

    orch.stream_manager.redis.get = AsyncMock(return_value=None)

    with (
        patch("backend.src.core.orchestrator.TaskRepository", return_value=mock_task_repo),
        patch("backend.src.core.orchestrator.ProjectRepository", return_value=mock_project_repo),
        patch("backend.src.core.orchestrator.PhaseRepository", return_value=mock_phase_repo),
        patch("backend.src.core.orchestrator.settings") as mock_settings,
        patch("backend.src.core.orchestrator.create_llm_client_from_project", return_value=mock_llm_client),
        patch("backend.src.core.orchestrator.get_prompt", return_value="mock prompt {failed_task_title} {failed_task_error} {failed_task_history} {done_tasks} {incomplete_tasks} {phase_name} {branch_name}"),
        patch.object(orch, "_mark_redesign_needs_intervention", new_callable=AsyncMock) as mock_intervene,
    ):
        mock_settings.max_auto_redesigns = 10
        await orch._process_escalation({"task_id": str(task.id)}, db)

    mock_intervene.assert_awaited_once()
    assert "invalid tasks format" in mock_intervene.call_args[0][1]


# ── Test: _process_escalation successful redesign ──────────────────


@pytest.mark.asyncio
async def test_process_escalation_successful_redesign() -> None:
    """Successful LLM call applies phase redesign and publishes event."""
    task = make_task(status=TaskStatus.redesign, qa_feedback_history=[])
    phase = make_phase(project_id=task.project_id)
    task.phase_id = phase.id
    orch = _build_orchestrator()
    db = _mock_db()

    mock_task_repo = AsyncMock()
    mock_task_repo.get_by_id = AsyncMock(return_value=task)
    mock_task_repo.list_incomplete_in_phase = AsyncMock(return_value=[task])
    mock_task_repo.list_done_in_phase = AsyncMock(return_value=[])

    mock_project = MagicMock()
    mock_project.id = task.project_id
    mock_project_repo = AsyncMock()
    mock_project_repo.get_by_id = AsyncMock(return_value=mock_project)

    mock_phase_repo = AsyncMock()
    mock_phase_repo.get_by_id = AsyncMock(return_value=phase)

    new_tasks = [{"id": str(task.id), "title": "Revised task", "description": "New approach"}]
    mock_llm_client = AsyncMock()
    mock_llm_client.structured_output = AsyncMock(return_value={"reasoning": "Simplified approach", "tasks": new_tasks})

    orch.stream_manager.redis.get = AsyncMock(return_value=None)

    with (
        patch("backend.src.core.orchestrator.TaskRepository", return_value=mock_task_repo),
        patch("backend.src.core.orchestrator.ProjectRepository", return_value=mock_project_repo),
        patch("backend.src.core.orchestrator.PhaseRepository", return_value=mock_phase_repo),
        patch("backend.src.core.orchestrator.settings") as mock_settings,
        patch("backend.src.core.orchestrator.create_llm_client_from_project", return_value=mock_llm_client),
        patch("backend.src.core.orchestrator.get_prompt", return_value="mock prompt {failed_task_title} {failed_task_error} {failed_task_history} {done_tasks} {incomplete_tasks} {phase_name} {branch_name}"),
        patch.object(orch, "_apply_phase_redesign", new_callable=AsyncMock) as mock_apply,
    ):
        mock_settings.max_auto_redesigns = 10
        await orch._process_escalation({"task_id": str(task.id)}, db)

    mock_apply.assert_awaited_once()
    # Should increment auto-redesign counter
    orch.stream_manager.redis.incr.assert_awaited()
    # Should publish board event
    orch.stream_manager.publish_board_event.assert_awaited()
    event_calls = orch.stream_manager.publish_board_event.call_args_list
    event_types = [c[0][0] for c in event_calls]
    assert "auto_redesign_applied" in event_types


# ── Test: _apply_phase_redesign deletes removed tasks ──────────────


@pytest.mark.asyncio
async def test_apply_phase_redesign_deletes_removed_tasks() -> None:
    """Tasks not in the LLM result list are deleted."""
    phase = make_phase()
    task_keep = make_task(status=TaskStatus.redesign, phase_id=phase.id, project_id=phase.project_id)
    task_delete = make_task(status=TaskStatus.redesign, phase_id=phase.id, project_id=phase.project_id)

    orch = _build_orchestrator()
    mock_task_repo = AsyncMock()
    mock_task_repo.hard_delete_many = AsyncMock()
    mock_task_repo.clear_dependencies = AsyncMock()
    mock_task_repo.add_dependencies = AsyncMock()
    db = _mock_db()

    new_task_list = [
        {"id": str(task_keep.id), "title": "Updated title", "worker_prompt": "new prompt"},
    ]

    await orch._apply_phase_redesign(
        phase=phase,
        incomplete_tasks=[task_keep, task_delete],
        new_task_list=new_task_list,
        reasoning="Simplified",
        task_repo=mock_task_repo,
        db=db,
    )

    mock_task_repo.hard_delete_many.assert_awaited_once()
    deleted_ids = mock_task_repo.hard_delete_many.call_args[0][0]
    assert task_delete.id in deleted_ids
    assert task_keep.id not in deleted_ids

    # Kept task should be updated
    assert task_keep.title == "Updated title"
    assert task_keep.worker_prompt == {"prompt": "new prompt"}
    assert task_keep.retry_count == 0


# ── Test: _apply_phase_redesign creates new tasks ──────────────────


@pytest.mark.asyncio
async def test_apply_phase_redesign_creates_new_tasks() -> None:
    """New tasks from LLM are created and added to the DB."""
    phase = make_phase()
    orch = _build_orchestrator()
    mock_task_repo = AsyncMock()
    mock_task_repo.hard_delete_many = AsyncMock()
    mock_task_repo.clear_dependencies = AsyncMock()
    mock_task_repo.add_dependencies = AsyncMock()
    db = _mock_db()

    new_task_list = [
        {
            "title": "Brand New Task",
            "description": "Do something new",
            "worker_prompt": "implement it",
            "qa_prompt": "check it",
            "priority": "high",
        },
    ]

    await orch._apply_phase_redesign(
        phase=phase,
        incomplete_tasks=[],
        new_task_list=new_task_list,
        reasoning="Complete rewrite",
        task_repo=mock_task_repo,
        db=db,
    )

    # New task should have been added to db
    db.add.assert_called_once()
    added_task = db.add.call_args[0][0]
    assert added_task.title == "Brand New Task"
    assert added_task.priority == TaskPriority.high
    assert added_task.status == TaskStatus.waiting


# ── Test: _apply_phase_redesign resets kept task fields ────────────


@pytest.mark.asyncio
async def test_apply_phase_redesign_resets_kept_task_fields() -> None:
    """Kept tasks have retry_count, qa_feedback_history, error_message, etc. reset."""
    phase = make_phase()
    task = make_task(
        status=TaskStatus.redesign,
        phase_id=phase.id,
        project_id=phase.project_id,
        retry_count=3,
        qa_feedback_history=[{"error": "old"}],
        error_message="old error",
    )
    task.commit_hash = "oldhash"
    task.started_at = datetime.now(timezone.utc)

    orch = _build_orchestrator()
    mock_task_repo = AsyncMock()
    mock_task_repo.hard_delete_many = AsyncMock()
    mock_task_repo.clear_dependencies = AsyncMock()
    mock_task_repo.add_dependencies = AsyncMock()
    db = _mock_db()

    new_task_list = [{"id": str(task.id), "title": task.title}]

    await orch._apply_phase_redesign(
        phase=phase,
        incomplete_tasks=[task],
        new_task_list=new_task_list,
        reasoning="Reset",
        task_repo=mock_task_repo,
        db=db,
    )

    assert task.retry_count == 0
    assert task.qa_feedback_history is None
    assert task.error_message is None
    assert task.commit_hash is None
    assert task.started_at is None
    # Should transition to waiting
    orch.state_machine.transition.assert_awaited_once()
    call_kwargs = orch.state_machine.transition.call_args.kwargs
    assert call_kwargs["new_status"] == TaskStatus.waiting


# ── Test: _process_escalation apply redesign failure triggers intervention


@pytest.mark.asyncio
async def test_process_escalation_apply_redesign_failure() -> None:
    """When _apply_phase_redesign raises, task is flagged for intervention."""
    task = make_task(status=TaskStatus.redesign, qa_feedback_history=[])
    phase = make_phase(project_id=task.project_id)
    task.phase_id = phase.id
    orch = _build_orchestrator()
    db = _mock_db()

    mock_task_repo = AsyncMock()
    mock_task_repo.get_by_id = AsyncMock(return_value=task)
    mock_task_repo.list_incomplete_in_phase = AsyncMock(return_value=[])
    mock_task_repo.list_done_in_phase = AsyncMock(return_value=[])

    mock_project = MagicMock()
    mock_project.id = task.project_id
    mock_project_repo = AsyncMock()
    mock_project_repo.get_by_id = AsyncMock(return_value=mock_project)

    mock_phase_repo = AsyncMock()
    mock_phase_repo.get_by_id = AsyncMock(return_value=phase)

    mock_llm_client = AsyncMock()
    mock_llm_client.structured_output = AsyncMock(return_value={"reasoning": "test", "tasks": []})

    orch.stream_manager.redis.get = AsyncMock(return_value=None)

    with (
        patch("backend.src.core.orchestrator.TaskRepository", return_value=mock_task_repo),
        patch("backend.src.core.orchestrator.ProjectRepository", return_value=mock_project_repo),
        patch("backend.src.core.orchestrator.PhaseRepository", return_value=mock_phase_repo),
        patch("backend.src.core.orchestrator.settings") as mock_settings,
        patch("backend.src.core.orchestrator.create_llm_client_from_project", return_value=mock_llm_client),
        patch("backend.src.core.orchestrator.get_prompt", return_value="mock {failed_task_title} {failed_task_error} {failed_task_history} {done_tasks} {incomplete_tasks} {phase_name} {branch_name}"),
        patch.object(orch, "_apply_phase_redesign", new_callable=AsyncMock, side_effect=RuntimeError("DB error")),
        patch.object(orch, "_mark_redesign_needs_intervention", new_callable=AsyncMock) as mock_intervene,
    ):
        mock_settings.max_auto_redesigns = 10
        await orch._process_escalation({"task_id": str(task.id)}, db)

    mock_intervene.assert_awaited_once()
    assert "Failed to apply redesign" in mock_intervene.call_args[0][1]


# ── Test: stop sets _running to False ──────────────────────────────


@pytest.mark.asyncio
async def test_stop_sets_running_false() -> None:
    """stop() sets _running to False."""
    orch = _build_orchestrator()
    orch._running = True
    await orch.stop()
    assert orch._running is False


# ── Test: _apply_phase_redesign wires up dependencies ──────────────


@pytest.mark.asyncio
async def test_apply_phase_redesign_wires_dependencies() -> None:
    """Dependencies referenced in new_task_list are wired up."""
    phase = make_phase()
    task_a = make_task(status=TaskStatus.redesign, phase_id=phase.id, project_id=phase.project_id, title="Task A")
    task_b = make_task(status=TaskStatus.redesign, phase_id=phase.id, project_id=phase.project_id, title="Task B")

    orch = _build_orchestrator()
    mock_task_repo = AsyncMock()
    mock_task_repo.hard_delete_many = AsyncMock()
    mock_task_repo.clear_dependencies = AsyncMock()
    mock_task_repo.add_dependencies = AsyncMock()
    db = _mock_db()

    new_task_list = [
        {"id": str(task_a.id), "title": "Task A"},
        {"id": str(task_b.id), "title": "Task B", "depends_on": [str(task_a.id)]},
    ]

    await orch._apply_phase_redesign(
        phase=phase,
        incomplete_tasks=[task_a, task_b],
        new_task_list=new_task_list,
        reasoning="Add dependency",
        task_repo=mock_task_repo,
        db=db,
    )

    mock_task_repo.clear_dependencies.assert_awaited()
    mock_task_repo.add_dependencies.assert_awaited()
    dep_call = mock_task_repo.add_dependencies.call_args
    assert task_a.id in dep_call[0][1]
