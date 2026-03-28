"""Tests for ExecutorRegistry — TDD: written before implementation."""

from __future__ import annotations

from typing import Any

import pytest

from backend.src.core.executor.base import ExecutionResult, ExecutorProtocol, ReviewResult
from backend.src.core.executor.registry import ExecutorRegistry


class FakeExecutor:
    """Minimal executor satisfying ExecutorProtocol for testing."""

    async def execute(self, prompt: str, context: dict[str, Any]) -> ExecutionResult:
        return ExecutionResult(success=True)

    async def review(self, prompt: str, context: dict[str, Any]) -> ReviewResult:
        return ReviewResult(passed=True)

    def supported_task_types(self) -> list[str]:
        return ["coding"]


class AnotherFakeExecutor:
    """Second executor for multi-registration tests."""

    async def execute(self, prompt: str, context: dict[str, Any]) -> ExecutionResult:
        return ExecutionResult(success=True)

    async def review(self, prompt: str, context: dict[str, Any]) -> ReviewResult:
        return ReviewResult(passed=True)

    def supported_task_types(self) -> list[str]:
        return ["refactor", "bugfix"]


@pytest.fixture
def registry() -> ExecutorRegistry:
    """Fresh registry for each test (no singleton leakage)."""
    reg = object.__new__(ExecutorRegistry)
    reg._executors = {}
    return reg


class TestRegister:
    def test_register_executor_factory(self, registry: ExecutorRegistry) -> None:
        registry.register("fake", FakeExecutor)
        assert "fake" in registry.list_available()

    def test_register_duplicate_raises(self, registry: ExecutorRegistry) -> None:
        registry.register("fake", FakeExecutor)
        with pytest.raises(KeyError, match="already registered"):
            registry.register("fake", FakeExecutor)


class TestGet:
    def test_get_returns_protocol_instance(self, registry: ExecutorRegistry) -> None:
        registry.register("fake", FakeExecutor)
        executor = registry.get("fake")
        assert isinstance(executor, ExecutorProtocol)

    def test_get_unknown_raises(self, registry: ExecutorRegistry) -> None:
        with pytest.raises(KeyError, match="not registered"):
            registry.get("nonexistent")

    def test_get_creates_new_instance_each_call(self, registry: ExecutorRegistry) -> None:
        registry.register("fake", FakeExecutor)
        a = registry.get("fake")
        b = registry.get("fake")
        assert a is not b


class TestListAvailable:
    def test_empty_registry(self, registry: ExecutorRegistry) -> None:
        assert registry.list_available() == []

    def test_multiple_registered(self, registry: ExecutorRegistry) -> None:
        registry.register("fake", FakeExecutor)
        registry.register("another", AnotherFakeExecutor)
        available = registry.list_available()
        assert sorted(available) == ["another", "fake"]
