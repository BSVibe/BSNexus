"""Tests for ClaudeCodeExecutor protocol compliance and registry integration."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from backend.src.core.executor.base import ExecutionResult, ExecutorProtocol, ReviewResult
from backend.src.core.executor.claude_code import ClaudeCodeExecutor
from backend.src.core.executor.registry import ExecutorRegistry


class TestClaudeCodeExecutorProtocolCompliance:
    """Verify ClaudeCodeExecutor satisfies ExecutorProtocol structurally."""

    @patch("backend.src.core.executor.claude_code.settings")
    def test_isinstance_check(self, mock_settings: Any) -> None:
        """ClaudeCodeExecutor must be recognized as an ExecutorProtocol instance."""
        mock_settings.workspace_dir = "/tmp/test"
        mock_settings.rate_limit_retry_count = 0
        mock_settings.rate_limit_wait_seconds = 1
        mock_settings.execution_timeout_seconds = 10
        mock_settings.total_execution_timeout_seconds = 30
        mock_settings.executor_skip_permissions = False
        executor = ClaudeCodeExecutor(workspace_dir="/tmp/test")
        assert isinstance(executor, ExecutorProtocol)

    @patch("backend.src.core.executor.claude_code.settings")
    def test_supported_task_types_returns_list(self, mock_settings: Any) -> None:
        """supported_task_types() must return a non-empty list of strings."""
        mock_settings.workspace_dir = "/tmp/test"
        mock_settings.rate_limit_retry_count = 0
        mock_settings.rate_limit_wait_seconds = 1
        mock_settings.execution_timeout_seconds = 10
        mock_settings.total_execution_timeout_seconds = 30
        mock_settings.executor_skip_permissions = False
        executor = ClaudeCodeExecutor(workspace_dir="/tmp/test")
        types = executor.supported_task_types()
        assert isinstance(types, list)
        assert len(types) > 0
        assert all(isinstance(t, str) for t in types)

    @patch("backend.src.core.executor.claude_code.settings")
    def test_supported_task_types_contains_coding(self, mock_settings: Any) -> None:
        """Must include 'coding' as a supported task type."""
        mock_settings.workspace_dir = "/tmp/test"
        mock_settings.rate_limit_retry_count = 0
        mock_settings.rate_limit_wait_seconds = 1
        mock_settings.execution_timeout_seconds = 10
        mock_settings.total_execution_timeout_seconds = 30
        mock_settings.executor_skip_permissions = False
        executor = ClaudeCodeExecutor(workspace_dir="/tmp/test")
        assert "coding" in executor.supported_task_types()

    @patch("backend.src.core.executor.claude_code.settings")
    def test_supported_task_types_expected_values(self, mock_settings: Any) -> None:
        """Must support coding, refactor, bugfix, test."""
        mock_settings.workspace_dir = "/tmp/test"
        mock_settings.rate_limit_retry_count = 0
        mock_settings.rate_limit_wait_seconds = 1
        mock_settings.execution_timeout_seconds = 10
        mock_settings.total_execution_timeout_seconds = 30
        mock_settings.executor_skip_permissions = False
        executor = ClaudeCodeExecutor(workspace_dir="/tmp/test")
        expected = {"coding", "refactor", "bugfix", "test"}
        assert set(executor.supported_task_types()) == expected

    @patch("backend.src.core.executor.claude_code.settings")
    def test_has_execute_method(self, mock_settings: Any) -> None:
        mock_settings.workspace_dir = "/tmp/test"
        mock_settings.rate_limit_retry_count = 0
        mock_settings.rate_limit_wait_seconds = 1
        mock_settings.execution_timeout_seconds = 10
        mock_settings.total_execution_timeout_seconds = 30
        mock_settings.executor_skip_permissions = False
        executor = ClaudeCodeExecutor(workspace_dir="/tmp/test")
        assert callable(getattr(executor, "execute", None))

    @patch("backend.src.core.executor.claude_code.settings")
    def test_has_review_method(self, mock_settings: Any) -> None:
        mock_settings.workspace_dir = "/tmp/test"
        mock_settings.rate_limit_retry_count = 0
        mock_settings.rate_limit_wait_seconds = 1
        mock_settings.execution_timeout_seconds = 10
        mock_settings.total_execution_timeout_seconds = 30
        mock_settings.executor_skip_permissions = False
        executor = ClaudeCodeExecutor(workspace_dir="/tmp/test")
        assert callable(getattr(executor, "review", None))


class TestClaudeCodeExecutorRegistration:
    """Verify ClaudeCodeExecutor can be registered and retrieved from registry."""

    @pytest.fixture
    def registry(self) -> ExecutorRegistry:
        """Fresh registry for each test (bypasses singleton)."""
        reg = object.__new__(ExecutorRegistry)
        reg._executors = {}
        return reg

    @patch("backend.src.core.executor.claude_code.settings")
    def test_register_and_get(self, mock_settings: Any, registry: ExecutorRegistry) -> None:
        """Can register ClaudeCodeExecutor factory and retrieve an instance."""
        mock_settings.workspace_dir = "/tmp/test"
        mock_settings.rate_limit_retry_count = 0
        mock_settings.rate_limit_wait_seconds = 1
        mock_settings.execution_timeout_seconds = 10
        mock_settings.total_execution_timeout_seconds = 30
        mock_settings.executor_skip_permissions = False
        registry.register("claude_code", ClaudeCodeExecutor)
        executor = registry.get("claude_code")
        assert isinstance(executor, ClaudeCodeExecutor)
        assert isinstance(executor, ExecutorProtocol)

    @patch("backend.src.core.executor.claude_code.settings")
    def test_registered_executor_has_correct_task_types(self, mock_settings: Any, registry: ExecutorRegistry) -> None:
        mock_settings.workspace_dir = "/tmp/test"
        mock_settings.rate_limit_retry_count = 0
        mock_settings.rate_limit_wait_seconds = 1
        mock_settings.execution_timeout_seconds = 10
        mock_settings.total_execution_timeout_seconds = 30
        mock_settings.executor_skip_permissions = False
        registry.register("claude_code", ClaudeCodeExecutor)
        executor = registry.get("claude_code")
        assert "coding" in executor.supported_task_types()


class TestCreateExecutorUsesRegistry:
    """Verify create_executor uses the registry."""

    @patch("backend.src.core.executor.claude_code.settings")
    def test_create_executor_claude_code(self, mock_settings: Any) -> None:
        mock_settings.workspace_dir = "/tmp/test"
        mock_settings.rate_limit_retry_count = 0
        mock_settings.rate_limit_wait_seconds = 1
        mock_settings.execution_timeout_seconds = 10
        mock_settings.total_execution_timeout_seconds = 30
        mock_settings.executor_skip_permissions = False
        from backend.src.core.executor import create_executor

        executor = create_executor("claude_code")
        assert isinstance(executor, ExecutorProtocol)

    @patch("backend.src.core.executor.claude_code.settings")
    def test_create_executor_default(self, mock_settings: Any) -> None:
        mock_settings.workspace_dir = "/tmp/test"
        mock_settings.rate_limit_retry_count = 0
        mock_settings.rate_limit_wait_seconds = 1
        mock_settings.execution_timeout_seconds = 10
        mock_settings.total_execution_timeout_seconds = 30
        mock_settings.executor_skip_permissions = False
        from backend.src.core.executor import create_executor

        executor = create_executor()
        assert isinstance(executor, ExecutorProtocol)

    def test_create_executor_unknown_raises(self) -> None:
        from backend.src.core.executor import create_executor

        with pytest.raises(KeyError, match="not registered"):
            create_executor("nonexistent_executor")
