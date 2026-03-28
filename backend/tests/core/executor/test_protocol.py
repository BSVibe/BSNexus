"""Tests for ExecutorProtocol — verifies the protocol contract."""

from typing import Any, runtime_checkable
from unittest.mock import AsyncMock

import pytest

from backend.src.core.executor.base import ExecutionResult, ExecutorProtocol, ReviewResult


class TestExecutorProtocol:
    """Verify ExecutorProtocol is a typing.Protocol with correct methods."""

    def test_protocol_is_runtime_checkable(self) -> None:
        assert hasattr(ExecutorProtocol, "__protocol_attrs__") or hasattr(
            ExecutorProtocol, "__abstractmethods__"
        ), "ExecutorProtocol should be a Protocol"

    def test_compliant_class_is_instance(self) -> None:
        """A class with the right methods satisfies the protocol structurally."""

        class _FakeExecutor:
            async def execute(self, prompt: str, context: dict[str, Any]) -> ExecutionResult:
                return ExecutionResult(success=True)

            async def review(self, prompt: str, context: dict[str, Any]) -> ReviewResult:
                return ReviewResult(passed=True)

            def supported_task_types(self) -> list[str]:
                return ["coding"]

        assert isinstance(_FakeExecutor(), ExecutorProtocol)

    def test_non_compliant_class_is_not_instance(self) -> None:
        """A class missing methods does NOT satisfy the protocol."""

        class _Incomplete:
            async def execute(self, prompt: str, context: dict[str, Any]) -> ExecutionResult:
                return ExecutionResult(success=True)

        assert not isinstance(_Incomplete(), ExecutorProtocol)

    def test_supported_task_types_method_required(self) -> None:
        """Protocol requires supported_task_types() method."""

        class _MissingSupportedTypes:
            async def execute(self, prompt: str, context: dict[str, Any]) -> ExecutionResult:
                return ExecutionResult(success=True)

            async def review(self, prompt: str, context: dict[str, Any]) -> ReviewResult:
                return ReviewResult(passed=True)

        assert not isinstance(_MissingSupportedTypes(), ExecutorProtocol)


class TestExecutionResult:
    """ExecutionResult dataclass tests."""

    def test_defaults(self) -> None:
        result = ExecutionResult(success=True)
        assert result.success is True
        assert result.output_path is None
        assert result.error_message is None
        assert result.stdout == ""
        assert result.stderr == ""
        assert result.error_category == ""

    def test_failure_with_error(self) -> None:
        result = ExecutionResult(
            success=False,
            error_message="something failed",
            error_category="tool",
        )
        assert result.success is False
        assert result.error_message == "something failed"
        assert result.error_category == "tool"


class TestReviewResult:
    """ReviewResult dataclass tests."""

    def test_defaults(self) -> None:
        result = ReviewResult(passed=True)
        assert result.passed is True
        assert result.feedback == ""
        assert result.error_message is None
        assert result.error_category == ""

    def test_failure_with_feedback(self) -> None:
        result = ReviewResult(passed=False, feedback="needs refactor")
        assert result.passed is False
        assert result.feedback == "needs refactor"
