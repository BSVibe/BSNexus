"""Tests for executor/litellm_executor.py — agentic loop."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.src.core.executor.litellm_executor import (
    ExecutionResult,
    LiteLLMExecutor,
)
from backend.src.tools.base import Tool, ToolCall, ToolContext, ToolDefinition, ToolResult
from backend.src.tools.cancellation import CancellationToken
from backend.src.tools.handler import ToolHandler


# ── Helpers ──────────────────────────────────────────────────────────


def _make_response(content: str = "hello", finish_reason: str = "stop", tool_calls: list | None = None):
    """Build a mock litellm response."""
    message = MagicMock()
    message.content = content
    message.tool_calls = tool_calls

    choice = MagicMock()
    choice.message = message
    choice.finish_reason = finish_reason

    usage = MagicMock()
    usage.prompt_tokens = 10
    usage.completion_tokens = 5
    usage.total_tokens = 15

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    response.model = "test-model"
    return response


def _make_tool_call_response(tool_name: str, arguments: dict[str, Any], call_id: str = "tc1"):
    """Build a mock response with tool_calls."""
    func = MagicMock()
    func.name = tool_name
    func.arguments = json.dumps(arguments)

    tc = MagicMock()
    tc.id = call_id
    tc.function = func

    return _make_response(content="", finish_reason="tool_calls", tool_calls=[tc])


class EchoTool(Tool):
    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echo"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"text": {"type": "string"}}}

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str:
        return f"echoed: {input.get('text', '')}"


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        project_id=uuid.uuid4(),
        workspace_path=tmp_path,
        workspace_type="server_managed",
        agent_id=uuid.uuid4(),
        agent_name="TestAgent",
        tenant_id=uuid.uuid4(),
        db_session_factory=AsyncMock(),
    )


@pytest.fixture(autouse=True)
def _clean_cancellation():
    yield
    CancellationToken._cancelled.clear()


# ── Tests ────────────────────────────────────────────────────────────


class TestLiteLLMExecutorNoTools:
    """Test simple text responses without tools."""

    @pytest.mark.asyncio
    @patch("backend.src.core.executor.litellm_executor.litellm")
    async def test_simple_text_response(self, mock_litellm: MagicMock) -> None:
        mock_litellm.acompletion = AsyncMock(return_value=_make_response("Hello!"))
        mock_litellm.completion_cost = MagicMock(return_value=0.001)

        executor = LiteLLMExecutor()
        result = await executor.execute(
            messages=[{"role": "user", "content": "Hi"}],
            tools=None,
            tool_handler=None,
            model="test-model",
            api_key="test-key",
        )

        assert result.content == "Hello!"
        assert result.stop_reason == "stop"
        assert result.iterations == 1
        assert result.prompt_tokens == 10
        assert result.completion_tokens == 5

    @pytest.mark.asyncio
    @patch("backend.src.core.executor.litellm_executor.litellm")
    async def test_cost_calculation(self, mock_litellm: MagicMock) -> None:
        mock_litellm.acompletion = AsyncMock(return_value=_make_response("ok"))
        mock_litellm.completion_cost = MagicMock(return_value=0.05)

        executor = LiteLLMExecutor()
        result = await executor.execute(
            messages=[{"role": "user", "content": "test"}],
            tools=None,
            tool_handler=None,
            model="test-model",
            api_key="key",
        )

        assert result.cost_usd == 0.05


class TestLiteLLMExecutorWithTools:
    """Test the agentic loop with tool calls."""

    @pytest.mark.asyncio
    @patch("backend.src.core.executor.litellm_executor.litellm")
    async def test_single_tool_call_then_response(self, mock_litellm: MagicMock, ctx: ToolContext) -> None:
        """LLM calls a tool, gets result, then responds."""
        tool_response = _make_tool_call_response("echo", {"text": "hi"})
        final_response = _make_response("Tool said: echoed: hi")
        mock_litellm.acompletion = AsyncMock(side_effect=[tool_response, final_response])
        mock_litellm.completion_cost = MagicMock(return_value=0.002)

        tool = EchoTool()
        handler = ToolHandler([tool], ctx)
        executor = LiteLLMExecutor()

        result = await executor.execute(
            messages=[{"role": "user", "content": "echo hi"}],
            tools=[tool.to_definition()],
            tool_handler=handler,
            model="test-model",
            api_key="key",
        )

        assert result.content == "Tool said: echoed: hi"
        assert len(result.tool_calls_made) == 1
        assert result.tool_calls_made[0].name == "echo"
        assert len(result.tool_results) == 1
        assert "echoed: hi" in result.tool_results[0].content
        assert result.iterations == 2

    @pytest.mark.asyncio
    @patch("backend.src.core.executor.litellm_executor.litellm")
    async def test_multiple_tool_calls_in_sequence(self, mock_litellm: MagicMock, ctx: ToolContext) -> None:
        """LLM calls tool twice before final response."""
        tc1 = _make_tool_call_response("echo", {"text": "a"}, "tc1")
        tc2 = _make_tool_call_response("echo", {"text": "b"}, "tc2")
        final = _make_response("Done with both")
        mock_litellm.acompletion = AsyncMock(side_effect=[tc1, tc2, final])
        mock_litellm.completion_cost = MagicMock(return_value=0.003)

        tool = EchoTool()
        handler = ToolHandler([tool], ctx)
        executor = LiteLLMExecutor()

        result = await executor.execute(
            messages=[{"role": "user", "content": "echo a then b"}],
            tools=[tool.to_definition()],
            tool_handler=handler,
            model="test-model",
            api_key="key",
        )

        assert result.content == "Done with both"
        assert len(result.tool_calls_made) == 2
        assert result.iterations == 3

    @pytest.mark.asyncio
    @patch("backend.src.core.executor.litellm_executor.litellm")
    async def test_max_iterations_guard(self, mock_litellm: MagicMock, ctx: ToolContext) -> None:
        """Executor stops after max_iterations even if LLM keeps calling tools."""
        infinite_tool_call = _make_tool_call_response("echo", {"text": "loop"})
        mock_litellm.acompletion = AsyncMock(return_value=infinite_tool_call)
        mock_litellm.completion_cost = MagicMock(return_value=0.0)

        tool = EchoTool()
        handler = ToolHandler([tool], ctx)
        executor = LiteLLMExecutor()

        result = await executor.execute(
            messages=[{"role": "user", "content": "loop"}],
            tools=[tool.to_definition()],
            tool_handler=handler,
            model="test-model",
            api_key="key",
            max_iterations=3,
        )

        assert result.stop_reason == "max_iterations"
        assert result.iterations == 3


class TestLiteLLMExecutorCancellation:
    @pytest.mark.asyncio
    @patch("backend.src.core.executor.litellm_executor.litellm")
    async def test_cancellation_stops_loop(self, mock_litellm: MagicMock) -> None:
        pid = uuid.uuid4()
        CancellationToken.cancel(pid)

        executor = LiteLLMExecutor()
        result = await executor.execute(
            messages=[{"role": "user", "content": "test"}],
            tools=None,
            tool_handler=None,
            model="test-model",
            api_key="key",
            project_id=pid,
        )

        assert result.stop_reason == "cancelled"
        assert result.iterations == 0
        # acompletion should never have been called
        assert not mock_litellm.acompletion.called


class TestLiteLLMExecutorEvents:
    @pytest.mark.asyncio
    @patch("backend.src.core.executor.litellm_executor.litellm")
    async def test_events_emitted(self, mock_litellm: MagicMock, ctx: ToolContext) -> None:
        tool_response = _make_tool_call_response("echo", {"text": "hi"})
        final_response = _make_response("done")
        mock_litellm.acompletion = AsyncMock(side_effect=[tool_response, final_response])
        mock_litellm.completion_cost = MagicMock(return_value=0.0)

        events: list = []
        tool = EchoTool()
        handler = ToolHandler([tool], ctx)
        executor = LiteLLMExecutor()

        await executor.execute(
            messages=[{"role": "user", "content": "test"}],
            tools=[tool.to_definition()],
            tool_handler=handler,
            model="test-model",
            api_key="key",
            on_event=lambda e: events.append(e),
        )

        event_types = [e.type for e in events]
        assert "iteration" in event_types
        assert "tool_start" in event_types
        assert "tool_end" in event_types
        assert "done" in event_types
