"""Tests for RunStateMachine transitions."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.src.core.state_machine import RunStateMachine
from backend.src.models.execution_run import RunStatus


def _fake_run(status: RunStatus = RunStatus.pending) -> SimpleNamespace:
    return SimpleNamespace(
        id="run-id",
        project_id="proj-id",
        request_id="req-id",
        status=status,
        error_message=None,
        started_at=None,
        completed_at=None,
    )


def test_can_transition_allowed():
    sm = RunStateMachine()
    assert sm.can_transition(RunStatus.pending, RunStatus.running)
    assert sm.can_transition(RunStatus.running, RunStatus.done)
    assert sm.can_transition(RunStatus.running, RunStatus.blocked)
    assert sm.can_transition(RunStatus.blocked, RunStatus.pending)


def test_can_transition_rejected():
    sm = RunStateMachine()
    assert not sm.can_transition(RunStatus.done, RunStatus.running)
    assert not sm.can_transition(RunStatus.done, RunStatus.pending)
    assert not sm.can_transition(RunStatus.blocked, RunStatus.done)
    assert not sm.can_transition(RunStatus.pending, RunStatus.done)


@pytest.mark.asyncio
async def test_transition_sets_started_at_on_running():
    sm = RunStateMachine()
    run = _fake_run(RunStatus.pending)

    await sm.transition(run, RunStatus.running)

    assert run.status == RunStatus.running
    assert run.started_at is not None


@pytest.mark.asyncio
async def test_transition_sets_completed_at_on_done():
    sm = RunStateMachine()
    run = _fake_run(RunStatus.running)

    await sm.transition(run, RunStatus.done)

    assert run.status == RunStatus.done
    assert run.completed_at is not None


@pytest.mark.asyncio
async def test_transition_records_error_on_blocked():
    sm = RunStateMachine()
    run = _fake_run(RunStatus.running)

    await sm.transition(run, RunStatus.blocked, reason="timeout")

    assert run.status == RunStatus.blocked
    assert run.error_message == "timeout"


@pytest.mark.asyncio
async def test_transition_clears_error_on_retry():
    sm = RunStateMachine()
    run = _fake_run(RunStatus.running)
    run.error_message = "old error"
    run.started_at = "some-time"

    await sm.transition(run, RunStatus.pending, reason="retry")

    assert run.status == RunStatus.pending
    assert run.error_message is None
    assert run.started_at is None


@pytest.mark.asyncio
async def test_transition_rejects_invalid():
    sm = RunStateMachine()
    run = _fake_run(RunStatus.done)

    with pytest.raises(ValueError, match="Invalid transition"):
        await sm.transition(run, RunStatus.running)
