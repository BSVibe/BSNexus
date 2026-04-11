"""Unit tests for the shared agent activity / status helpers."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from backend.src.core.agent_activity import (
    busy_agent_ids,
    clear_agent_busy,
    mark_agent_busy,
    resolve_agent_runtime_status,
    resolve_agent_status_dot,
)
from backend.src.models import Agent, Task, TaskPriority, TaskSource, TaskStatus, TaskType


def _agent(*, status: str = "online", executor_type: str = "claude_api") -> Agent:
    return Agent(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        name="A",
        role="dev",
        title="Developer",
        executor_type=executor_type,
        executor_config={},
        capabilities=[],
        status=status,
        is_active=True,
    )


def _task(status: TaskStatus) -> Task:
    return Task(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        phase_id=uuid.uuid4(),
        title="t",
        status=status,
        priority=TaskPriority.medium,
        task_type=TaskType.feature,
        source=TaskSource.llm,
        version=1,
    )


# ── resolve_agent_status_dot ─────────────────────────────────────────


def test_dot_blocked_task_wins_over_busy() -> None:
    agent = _agent(status="online")
    dot = resolve_agent_status_dot(
        agent, current_task=_task(TaskStatus.blocked), is_busy=True
    )
    assert dot == "red"


def test_dot_running_task_wins_over_busy() -> None:
    agent = _agent(status="online")
    dot = resolve_agent_status_dot(
        agent, current_task=_task(TaskStatus.running), is_busy=True
    )
    assert dot == "green"


def test_dot_busy_without_task_is_blue() -> None:
    agent = _agent(status="online")
    assert resolve_agent_status_dot(agent, is_busy=True) == "green"


def test_dot_online_without_task_is_yellow() -> None:
    agent = _agent(status="online")
    assert resolve_agent_status_dot(agent) == "yellow"


def test_dot_offline_non_worker_is_gray() -> None:
    agent = _agent(status="offline", executor_type="claude_api")
    assert resolve_agent_status_dot(agent) == "gray"


def test_dot_offline_worker_with_online_pool_is_yellow() -> None:
    """Worker-typed agents inherit availability from the worker pool.

    The Plan tab and the Agents tab used to disagree because the Plan
    tab only looked at agent.status — this regression check makes them
    return the same dot for the same situation.
    """
    agent = _agent(status="offline", executor_type="worker")
    assert resolve_agent_status_dot(agent, online_worker_available=True) == "yellow"
    assert resolve_agent_status_dot(agent, online_worker_available=False) == "gray"


def test_dot_status_busy_column_maps_to_blue() -> None:
    """Persistent ``busy`` from the heartbeat lifecycle should not show as idle."""
    agent = _agent(status="busy")
    assert resolve_agent_status_dot(agent) == "green"


# ── resolve_agent_runtime_status ─────────────────────────────────────


def test_runtime_status_busy_short_circuit() -> None:
    agent = _agent(status="online")
    assert resolve_agent_runtime_status(agent, is_busy=True) == "busy"


def test_runtime_status_offline_worker_promoted_when_pool_online() -> None:
    agent = _agent(status="offline", executor_type="worker")
    assert resolve_agent_runtime_status(agent, online_worker_available=True) == "online"
    assert resolve_agent_runtime_status(agent, online_worker_available=False) == "offline"


def test_runtime_status_passes_through_known_values() -> None:
    assert resolve_agent_runtime_status(_agent(status="online")) == "online"
    assert resolve_agent_runtime_status(_agent(status="busy")) == "busy"
    assert resolve_agent_runtime_status(_agent(status="offline", executor_type="claude_api")) == "offline"


# ── BusyAgentTracker (Redis-backed) ──────────────────────────────────


@pytest.mark.asyncio
async def test_mark_and_clear_agent_busy_no_redis_is_safe() -> None:
    """Calls must not raise when redis is None — keeps tests + dev simple."""
    tenant = uuid.uuid4()
    aid = uuid.uuid4()
    await mark_agent_busy(None, tenant, aid)
    await clear_agent_busy(None, tenant, aid)
    assert await busy_agent_ids(None, tenant, [aid]) == set()


@pytest.mark.asyncio
async def test_busy_agent_ids_returns_only_set_keys() -> None:
    """Mock redis MGET; only entries with a non-None value are reported busy."""
    redis = AsyncMock()
    busy_id = uuid.uuid4()
    idle_id = uuid.uuid4()
    redis.mget = AsyncMock(return_value=[b"1", None])

    result = await busy_agent_ids(redis, uuid.uuid4(), [busy_id, idle_id])
    assert result == {busy_id}
    assert idle_id not in result


@pytest.mark.asyncio
async def test_mark_agent_busy_uses_ttl() -> None:
    """``mark_agent_busy`` must always set a TTL — otherwise a crash leaks."""
    redis = AsyncMock()
    redis.set = AsyncMock()
    await mark_agent_busy(redis, uuid.uuid4(), uuid.uuid4())
    redis.set.assert_awaited_once()
    _, kwargs = redis.set.call_args
    assert "ex" in kwargs and kwargs["ex"] > 0


@pytest.mark.asyncio
async def test_busy_agent_ids_swallows_redis_errors() -> None:
    redis = AsyncMock()
    redis.mget = AsyncMock(side_effect=RuntimeError("boom"))
    result = await busy_agent_ids(redis, uuid.uuid4(), [uuid.uuid4()])
    assert result == set()


@pytest.mark.asyncio
async def test_mark_agent_busy_swallows_redis_errors() -> None:
    redis = AsyncMock()
    redis.set = AsyncMock(side_effect=RuntimeError("boom"))
    # Must not raise — mark/clear are best-effort transient state.
    await mark_agent_busy(redis, uuid.uuid4(), uuid.uuid4())


@pytest.mark.asyncio
async def test_clear_agent_busy_swallows_redis_errors() -> None:
    redis = AsyncMock()
    redis.delete = AsyncMock(side_effect=RuntimeError("boom"))
    await clear_agent_busy(redis, uuid.uuid4(), uuid.uuid4())


@pytest.mark.asyncio
async def test_busy_agent_ids_empty_list_short_circuits() -> None:
    """Empty agent list must NOT call redis (saves a roundtrip)."""
    redis = AsyncMock()
    redis.mget = AsyncMock()
    result = await busy_agent_ids(redis, uuid.uuid4(), [])
    assert result == set()
    redis.mget.assert_not_called()


def test_runtime_status_passes_through_unknown_status_for_non_worker() -> None:
    """Non-canonical status strings are passed through verbatim for non-workers.

    The Agents tab tolerates these and the heartbeat lifecycle owns the
    canonical vocabulary; defaulting to ``offline`` would hide a worker
    that briefly reports a custom state.
    """
    agent = _agent(status="weird_state", executor_type="claude_api")
    assert resolve_agent_runtime_status(agent) == "weird_state"


def test_runtime_status_blank_status_defaults_to_offline_for_non_worker() -> None:
    agent = _agent(status="", executor_type="claude_api")
    assert resolve_agent_runtime_status(agent) == "offline"
