"""Tests for TaskRunner agent resolution chain."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from backend.src.core.executor.base import ExecutionResult, ExecutorProtocol
from backend.src.core.task_runner import LocalTaskRunner
from dataclasses import dataclass


@dataclass
class FakeAgent:
    """Lightweight stand-in for Agent model in unit tests."""
    executor_type: str = "claude_api"
    executor_config: dict | None = None
    system_prompt: str | None = None


def _make_task(**kwargs):
    """Create a minimal mock task for TaskRunner tests."""
    from unittest.mock import MagicMock
    task = MagicMock(spec=[
        "id", "title", "branch_name", "worker_prompt", "qa_prompt",
        "executor_type", "retry_count", "qa_feedback_history",
    ])
    task.id = kwargs.get("id", "test-task-id")
    task.title = kwargs.get("title", "Test task")
    task.branch_name = kwargs.get("branch_name", "")
    task.worker_prompt = kwargs.get("worker_prompt", {"prompt": "do stuff"})
    task.qa_prompt = kwargs.get("qa_prompt", {"prompt": "review"})
    task.executor_type = kwargs.get("executor_type", "claude_code")
    task.retry_count = kwargs.get("retry_count", 0)
    task.qa_feedback_history = kwargs.get("qa_feedback_history", [])
    return task


class TestAgentResolution:
    @pytest.mark.asyncio
    async def test_no_bound_agent_uses_task_executor_type(self) -> None:
        mock_executor = AsyncMock(spec=ExecutorProtocol)
        mock_executor.execute.return_value = ExecutionResult(success=True)

        runner = LocalTaskRunner(executor=mock_executor)
        task = _make_task(executor_type="claude_code")

        await runner.execute_task(task, repo_path="")
        mock_executor.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_bound_agent_overrides_executor_type(self) -> None:
        mock_executor = AsyncMock(spec=ExecutorProtocol)
        mock_executor.execute.return_value = ExecutionResult(success=True)

        runner = LocalTaskRunner(executor=mock_executor)
        task = _make_task(executor_type="claude_code")

        # Bind an Agent
        agent = FakeAgent(
            executor_type="bsgateway",
            executor_config={"model": "opus"},
            system_prompt="You are a senior engineer.",
        )
        task._bound_agent = agent

        await runner.execute_task(task, repo_path="")

        # Check that system prompt was injected
        prompt_arg = mock_executor.execute.call_args[0][0]
        assert "You are a senior engineer" in prompt_arg
        assert "do stuff" in prompt_arg

    @pytest.mark.asyncio
    async def test_bound_agent_config_merged_into_context(self) -> None:
        mock_executor = AsyncMock(spec=ExecutorProtocol)
        mock_executor.execute.return_value = ExecutionResult(success=True)

        runner = LocalTaskRunner(executor=mock_executor)
        task = _make_task()

        agent = FakeAgent(
            executor_type="claude_api",
            executor_config={"temperature": 0.5, "max_tokens": 8192},
            system_prompt=None,
        )
        task._bound_agent = agent

        await runner.execute_task(task, repo_path="")

        context_arg = mock_executor.execute.call_args[0][1]
        assert context_arg.get("temperature") == 0.5
        assert context_arg.get("max_tokens") == 8192

    @pytest.mark.asyncio
    async def test_no_agent_no_system_prompt(self) -> None:
        mock_executor = AsyncMock(spec=ExecutorProtocol)
        mock_executor.execute.return_value = ExecutionResult(success=True)

        runner = LocalTaskRunner(executor=mock_executor)
        task = _make_task(worker_prompt={"prompt": "plain task"})

        await runner.execute_task(task, repo_path="")

        prompt_arg = mock_executor.execute.call_args[0][0]
        assert prompt_arg == "plain task"
        assert "Agent Context" not in prompt_arg
