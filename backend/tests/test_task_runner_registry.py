"""Tests for LocalTaskRunner registry-based executor resolution."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.src.core.executor.base import ExecutionResult, ExecutorProtocol, ReviewResult
from backend.src.core.task_runner import LocalTaskRunner


def _make_mock_executor() -> AsyncMock:
    """Create a mock executor satisfying ExecutorProtocol."""
    executor = AsyncMock(spec=ExecutorProtocol)
    executor.supported_task_types.return_value = ["coding"]
    return executor


def _make_task(
    *,
    title: str = "Test task",
    branch_name: str | None = "feature/test",
    worker_prompt: dict | str | None = None,
    qa_prompt: dict | str | None = None,
    retry_count: int = 0,
    qa_feedback_history: list | None = None,
    executor_type: str = "coding",
) -> MagicMock:
    """Create a mock Task with executor_type field."""
    task = MagicMock()
    task.id = uuid.uuid4()
    task.title = title
    task.branch_name = branch_name
    task.worker_prompt = worker_prompt
    task.qa_prompt = qa_prompt
    task.retry_count = retry_count
    task.qa_feedback_history = qa_feedback_history or []
    task.executor_type = executor_type
    return task


class TestLocalTaskRunnerRegistryInit:
    """Test LocalTaskRunner construction with executor_name."""

    def test_default_executor_name(self) -> None:
        """LocalTaskRunner defaults to 'claude_code' executor."""
        runner = LocalTaskRunner()
        assert runner._default_executor_name == "claude_code"

    def test_custom_executor_name(self) -> None:
        """LocalTaskRunner accepts a custom executor_name."""
        runner = LocalTaskRunner(executor_name="custom_executor")
        assert runner._default_executor_name == "custom_executor"


class TestLocalTaskRunnerResolveExecutor:
    """Test executor resolution from registry."""

    def test_resolve_by_task_executor_type(self) -> None:
        """Executor is resolved using task.executor_type when available."""
        mock_executor = _make_mock_executor()
        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_executor

        runner = LocalTaskRunner(executor_name="claude_code")

        with patch("backend.src.core.task_runner.ExecutorRegistry", return_value=mock_registry):
            resolved = runner._resolve_executor("custom_type")

        mock_registry.get.assert_called_once_with("custom_type")
        assert resolved is mock_executor

    def test_resolve_falls_back_to_default(self) -> None:
        """Falls back to default executor_name when task.executor_type lookup fails."""
        mock_executor = _make_mock_executor()
        mock_registry = MagicMock()
        mock_registry.get.side_effect = [KeyError("not found"), mock_executor]

        runner = LocalTaskRunner(executor_name="claude_code")

        with patch("backend.src.core.task_runner.ExecutorRegistry", return_value=mock_registry):
            resolved = runner._resolve_executor("unknown_type")

        assert mock_registry.get.call_count == 2
        mock_registry.get.assert_any_call("unknown_type")
        mock_registry.get.assert_any_call("claude_code")
        assert resolved is mock_executor

    def test_resolve_uses_default_when_executor_type_is_default_coding(self) -> None:
        """When executor_type is 'coding' (Task model default), use the default executor_name directly."""
        mock_executor = _make_mock_executor()
        mock_registry = MagicMock()
        # 'coding' won't be in registry, falls back to default
        mock_registry.get.side_effect = [KeyError("not found"), mock_executor]

        runner = LocalTaskRunner(executor_name="claude_code")

        with patch("backend.src.core.task_runner.ExecutorRegistry", return_value=mock_registry):
            resolved = runner._resolve_executor("coding")

        assert resolved is mock_executor


class TestLocalTaskRunnerExecuteWithRegistry:
    """Test execute_task uses registry-resolved executor."""

    @pytest.mark.asyncio
    async def test_execute_task_resolves_from_registry(self) -> None:
        """execute_task resolves executor via registry using task.executor_type."""
        mock_executor = _make_mock_executor()
        mock_executor.execute.return_value = ExecutionResult(success=True)

        runner = LocalTaskRunner(executor_name="claude_code")
        task = _make_task(worker_prompt={"prompt": "write code"}, executor_type="claude_code")

        with patch.object(runner, "_resolve_executor", return_value=mock_executor) as mock_resolve:
            result = await runner.execute_task(task, repo_path="")

        mock_resolve.assert_called_once_with("claude_code")
        assert result.success is True
        mock_executor.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_task_uses_task_executor_type(self) -> None:
        """execute_task passes task.executor_type to resolver."""
        mock_executor = _make_mock_executor()
        mock_executor.execute.return_value = ExecutionResult(success=True)

        runner = LocalTaskRunner(executor_name="claude_code")
        task = _make_task(worker_prompt={"prompt": "refactor"}, executor_type="special_executor")

        with patch.object(runner, "_resolve_executor", return_value=mock_executor) as mock_resolve:
            result = await runner.execute_task(task, repo_path="")

        mock_resolve.assert_called_once_with("special_executor")
        assert result.success is True


class TestLocalTaskRunnerReviewWithRegistry:
    """Test review_task uses registry-resolved executor."""

    @pytest.mark.asyncio
    async def test_review_task_resolves_from_registry(self) -> None:
        """review_task resolves executor via registry using task.executor_type."""
        mock_executor = _make_mock_executor()
        mock_executor.review.return_value = ReviewResult(passed=True, feedback="")

        runner = LocalTaskRunner(executor_name="claude_code")
        task = _make_task(qa_prompt={"prompt": "review"}, executor_type="claude_code")

        with patch.object(runner, "_resolve_executor", return_value=mock_executor) as mock_resolve:
            result = await runner.review_task(task, repo_path="")

        mock_resolve.assert_called_once_with("claude_code")
        assert result.passed is True
        mock_executor.review.assert_awaited_once()


class TestLocalTaskRunnerBackwardCompatibility:
    """Verify backward compatibility with executor parameter."""

    def test_executor_param_still_works(self) -> None:
        """Passing executor= directly should still work for backward compat."""
        mock_executor = _make_mock_executor()
        runner = LocalTaskRunner(executor=mock_executor)
        assert runner._executor is mock_executor

    @pytest.mark.asyncio
    async def test_executor_param_used_over_registry(self) -> None:
        """When executor= is passed, it's used directly instead of registry lookup."""
        mock_executor = _make_mock_executor()
        mock_executor.execute.return_value = ExecutionResult(success=True)

        runner = LocalTaskRunner(executor=mock_executor)
        task = _make_task(worker_prompt="code", executor_type="anything")

        result = await runner.execute_task(task, repo_path="")

        assert result.success is True
        mock_executor.execute.assert_awaited_once()
