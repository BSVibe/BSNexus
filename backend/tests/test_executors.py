"""Tests for executor implementations."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.src.core.executor.base import ExecutorCapability
from backend.src.core.executor.bsgateway import BSGatewayExecutor, INFO as BSGATEWAY_INFO
from backend.src.core.executor.generic_llm import GenericLLMExecutor
from backend.src.core.executor.codex import CodexExecutor, INFO as CODEX_INFO
from backend.src.core.executor.claude_code import INFO as CLAUDE_CODE_INFO
from backend.src.core.executor.registry import ExecutorRegistry


class TestExecutorInfo:
    def test_claude_code_info(self) -> None:
        assert CLAUDE_CODE_INFO.name == "claude_code"
        assert CLAUDE_CODE_INFO.requires_local is True
        assert CLAUDE_CODE_INFO.requires_workspace is True
        assert ExecutorCapability.coding in CLAUDE_CODE_INFO.capabilities

    def test_bsgateway_info(self) -> None:
        assert BSGATEWAY_INFO.name == "bsgateway"
        assert BSGATEWAY_INFO.requires_local is False
        assert len(BSGATEWAY_INFO.capabilities) >= 5

    def test_codex_info(self) -> None:
        assert CODEX_INFO.name == "codex"
        assert ExecutorCapability.coding in CODEX_INFO.capabilities
        assert CODEX_INFO.requires_local is False


class TestGenericLLMExecutor:
    """GenericLLMExecutor is now an alias for LiteLLMExecutor (agentic loop).
    Detailed agentic loop tests are in test_tools/test_litellm_executor.py."""

    def test_is_alias_for_litellm_executor(self) -> None:
        from backend.src.core.executor.litellm_executor import LiteLLMExecutor
        assert GenericLLMExecutor is LiteLLMExecutor


class TestBSGatewayExecutor:
    @pytest.mark.asyncio
    @patch("backend.src.core.executor.bsgateway.acompletion")
    async def test_execute_uses_routing_hint(self, mock_acompletion) -> None:
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="Result"))]
        mock_acompletion.return_value = mock_response

        executor = BSGatewayExecutor()
        result = await executor.execute("Task", {"routing_hint": "complex"})

        assert result.success is True
        call_kwargs = mock_acompletion.call_args
        assert "openai/complex" in str(call_kwargs)

    @pytest.mark.asyncio
    @patch("backend.src.core.executor.bsgateway.acompletion")
    async def test_execute_default_auto_routing(self, mock_acompletion) -> None:
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="Result"))]
        mock_acompletion.return_value = mock_response

        executor = BSGatewayExecutor()
        await executor.execute("Task", {})

        call_kwargs = mock_acompletion.call_args
        assert "openai/auto" in str(call_kwargs)


class TestCodexExecutor:
    @pytest.mark.asyncio
    @patch("backend.src.core.executor.codex.acompletion")
    async def test_execute_success(self, mock_acompletion) -> None:
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="def hello(): pass"))]
        mock_acompletion.return_value = mock_response

        executor = CodexExecutor()
        result = await executor.execute("Write hello function", {})

        assert result.success is True

    def test_supported_task_types_coding_only(self) -> None:
        executor = CodexExecutor()
        types = executor.supported_task_types()
        assert "coding" in types
        assert "writing" not in types


class TestExecutorRegistryCapabilities:
    def test_list_by_capability_coding(self) -> None:
        registry = ExecutorRegistry()
        coding_executors = registry.list_by_capability(ExecutorCapability.coding)
        assert "claude_code" in coding_executors
        assert "generic_llm" in coding_executors
        assert "codex" in coding_executors

    def test_list_by_capability_writing(self) -> None:
        registry = ExecutorRegistry()
        writing_executors = registry.list_by_capability(ExecutorCapability.writing)
        assert "generic_llm" in writing_executors
        assert "bsgateway" in writing_executors
        assert "claude_code" not in writing_executors

    def test_get_info(self) -> None:
        registry = ExecutorRegistry()
        info = registry.get_info("claude_code")
        assert info is not None
        assert info.requires_local is True

    def test_get_info_unknown(self) -> None:
        registry = ExecutorRegistry()
        info = registry.get_info("nonexistent")
        assert info is None
