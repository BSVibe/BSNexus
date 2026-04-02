"""Tests for PMOrchestrator exponential backoff retry logic."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.src.core.orchestrator import PMOrchestrator

pytestmark = pytest.mark.asyncio


def _make_orchestrator() -> PMOrchestrator:
    stream_manager = AsyncMock()
    stream_manager.redis = AsyncMock()
    stream_manager.redis.get = AsyncMock(return_value=None)
    stream_manager.redis.set = AsyncMock()
    task_runner = MagicMock()
    state_machine = MagicMock()
    return PMOrchestrator(
        stream_manager=stream_manager,
        task_runner=task_runner,
        state_machine=state_machine,
    )


@asynccontextmanager
async def _mock_session():
    yield AsyncMock()


def _session_factory():
    return _mock_session()


async def test_start_retries_on_crash() -> None:
    """start() retries with backoff when main loops crash."""
    orch = _make_orchestrator()
    project_id = uuid.uuid4()
    call_count = 0

    async def _crashing_gather(*coros):
        nonlocal call_count
        call_count += 1
        for c in coros:
            c.close()  # clean up coroutines
        if call_count <= 2:
            raise RuntimeError("simulated crash")
        # On 3rd attempt, stop cleanly
        orch._running = False

    with (
        patch.object(orch, "_promote_waiting_tasks", new_callable=AsyncMock),
        patch.object(orch, "_recover_orphaned_redesign_tasks", new_callable=AsyncMock),
        patch.object(orch, "_execution_loop", new_callable=AsyncMock),
        patch.object(orch, "_escalation_loop", new_callable=AsyncMock),
        patch("backend.src.core.orchestrator.asyncio.gather", side_effect=_crashing_gather),
        patch("backend.src.core.orchestrator.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        await orch.start(project_id, _session_factory)

    assert call_count == 3
    # Should have slept twice (after attempt 1 and 2)
    assert mock_sleep.await_count == 2
    # Backoff: first delay = 2s, second = 4s
    delays = [call.args[0] for call in mock_sleep.await_args_list]
    assert delays[0] == 2.0
    assert delays[1] == 4.0


async def test_start_gives_up_after_max_retries() -> None:
    """start() raises after exceeding max retries."""
    orch = _make_orchestrator()
    project_id = uuid.uuid4()

    async def _always_crash(*coros):
        for c in coros:
            c.close()
        raise RuntimeError("permanent failure")

    with (
        patch.object(orch, "_promote_waiting_tasks", new_callable=AsyncMock),
        patch.object(orch, "_recover_orphaned_redesign_tasks", new_callable=AsyncMock),
        patch.object(orch, "_execution_loop", new_callable=AsyncMock),
        patch.object(orch, "_escalation_loop", new_callable=AsyncMock),
        patch("backend.src.core.orchestrator.asyncio.gather", side_effect=_always_crash),
        patch("backend.src.core.orchestrator.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        with pytest.raises(RuntimeError, match="permanent failure"):
            await orch.start(project_id, _session_factory)

    # Should have slept 5 times (retries 1-5) before giving up on attempt 6
    assert mock_sleep.await_count == 5


async def test_start_clean_exit_no_retry() -> None:
    """start() exits cleanly without retrying when loops complete normally."""
    orch = _make_orchestrator()
    project_id = uuid.uuid4()

    async def _clean_exit(*coros):
        for c in coros:
            c.close()
        # Normal completion — no exception

    with (
        patch.object(orch, "_promote_waiting_tasks", new_callable=AsyncMock),
        patch.object(orch, "_recover_orphaned_redesign_tasks", new_callable=AsyncMock),
        patch.object(orch, "_execution_loop", new_callable=AsyncMock),
        patch.object(orch, "_escalation_loop", new_callable=AsyncMock),
        patch("backend.src.core.orchestrator.asyncio.gather", side_effect=_clean_exit),
        patch("backend.src.core.orchestrator.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        await orch.start(project_id, _session_factory)

    mock_sleep.assert_not_awaited()


async def test_start_stops_retrying_when_running_is_false() -> None:
    """start() does not retry when _running is set to False."""
    orch = _make_orchestrator()
    project_id = uuid.uuid4()

    async def _crash_and_stop(*coros):
        for c in coros:
            c.close()
        orch._running = False
        raise RuntimeError("crash after stop")

    with (
        patch.object(orch, "_promote_waiting_tasks", new_callable=AsyncMock),
        patch.object(orch, "_recover_orphaned_redesign_tasks", new_callable=AsyncMock),
        patch.object(orch, "_execution_loop", new_callable=AsyncMock),
        patch.object(orch, "_escalation_loop", new_callable=AsyncMock),
        patch("backend.src.core.orchestrator.asyncio.gather", side_effect=_crash_and_stop),
        patch("backend.src.core.orchestrator.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        with pytest.raises(RuntimeError, match="crash after stop"):
            await orch.start(project_id, _session_factory)

    # Should NOT have slept — immediate re-raise because _running=False
    mock_sleep.assert_not_awaited()
