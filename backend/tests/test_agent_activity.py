"""Unit tests for the agent status resolution — task-derived, no Redis busy keys."""

from __future__ import annotations

import uuid

from backend.src.core.agent_activity import (
    resolve_agent_runtime_status,
    resolve_agent_status_dot,
)
from backend.src.models import Agent, Task, TaskPriority, TaskSource, TaskStatus, TaskType


def _agent(*, status: str = "online", executor_type: str = "generic_llm",
           executor_config_id: uuid.UUID | None = None) -> Agent:
    return Agent(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        name="A",
        role="dev",
        title="Developer",
        executor_type=executor_type,
        executor_config={},
        executor_config_id=executor_config_id,
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


# ── resolve_agent_status_dot ───────���─────────────────────────────────


def test_dot_blocked_task_is_red() -> None:
    agent = _agent(status="online")
    dot = resolve_agent_status_dot(agent, current_task=_task(TaskStatus.blocked))
    assert dot == "red"


def test_dot_running_task_is_green() -> None:
    agent = _agent(status="online")
    dot = resolve_agent_status_dot(agent, current_task=_task(TaskStatus.running))
    assert dot == "green"


def test_dot_no_task_llm_api_with_config_is_yellow() -> None:
    config_id = uuid.uuid4()
    agent = _agent(status="online", executor_config_id=config_id)
    assert resolve_agent_status_dot(agent) == "yellow"


def test_dot_no_task_online_agent_is_yellow() -> None:
    agent = _agent(status="online")
    assert resolve_agent_status_dot(agent) == "yellow"


def test_dot_offline_non_worker_no_config_is_gray() -> None:
    agent = _agent(status="offline", executor_type="generic_llm")
    assert resolve_agent_status_dot(agent) == "gray"


def test_dot_offline_worker_with_online_pool_is_yellow() -> None:
    agent = _agent(status="offline", executor_type="worker")
    assert resolve_agent_status_dot(agent, online_worker_available=True) == "yellow"
    assert resolve_agent_status_dot(agent, online_worker_available=False) == "gray"


def test_dot_pending_task_does_not_make_green() -> None:
    """A pending task is not actively being worked — agent should show idle."""
    agent = _agent(status="online")
    dot = resolve_agent_status_dot(agent, current_task=_task(TaskStatus.pending))
    assert dot == "yellow"


def test_dot_done_task_does_not_make_green() -> None:
    agent = _agent(status="online")
    dot = resolve_agent_status_dot(agent, current_task=_task(TaskStatus.done))
    assert dot == "yellow"


# ── resolve_agent_runtime_status ───��─────────────────────────────────


def test_runtime_status_running_task_is_busy() -> None:
    agent = _agent(status="online")
    assert resolve_agent_runtime_status(
        agent, current_task=_task(TaskStatus.running)
    ) == "busy"


def test_runtime_status_blocked_task_is_busy() -> None:
    agent = _agent(status="online")
    assert resolve_agent_runtime_status(
        agent, current_task=_task(TaskStatus.blocked)
    ) == "busy"


def test_runtime_status_no_task_online() -> None:
    agent = _agent(status="online")
    assert resolve_agent_runtime_status(agent) == "online"


def test_runtime_status_llm_api_with_config_is_online() -> None:
    config_id = uuid.uuid4()
    agent = _agent(status="offline", executor_config_id=config_id)
    assert resolve_agent_runtime_status(agent) == "online"


def test_runtime_status_offline_worker_with_pool() -> None:
    agent = _agent(status="offline", executor_type="worker")
    assert resolve_agent_runtime_status(agent, online_worker_available=True) == "online"
    assert resolve_agent_runtime_status(agent, online_worker_available=False) == "offline"


def test_runtime_status_blank_defaults_to_offline() -> None:
    agent = _agent(status="", executor_type="generic_llm")
    assert resolve_agent_runtime_status(agent) == "offline"
