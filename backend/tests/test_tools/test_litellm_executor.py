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


class _MockStreamChunk:
    """A single chunk in a streaming response."""

    def __init__(
        self,
        content: str | None = None,
        finish_reason: str | None = None,
        tool_calls: list | None = None,
        usage: dict | None = None,
    ):
        delta = MagicMock()
        delta.content = content
        delta.tool_calls = tool_calls or []

        choice = MagicMock()
        choice.delta = delta
        choice.finish_reason = finish_reason

        self.choices = [choice]

        if usage:
            u = MagicMock()
            u.prompt_tokens = usage.get("prompt_tokens", 0)
            u.completion_tokens = usage.get("completion_tokens", 0)
            u.total_tokens = usage.get("total_tokens", 0)
            self.usage = u
        else:
            self.usage = None


class _MockStream:
    """Async iterator that yields streaming chunks."""

    def __init__(self, chunks: list[_MockStreamChunk]):
        self._chunks = chunks
        self._idx = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._idx >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._idx]
        self._idx += 1
        return chunk


def _make_stream(content: str = "hello", finish_reason: str = "stop") -> _MockStream:
    """Build a mock streaming response for simple text."""
    chunks = [
        _MockStreamChunk(content=content),
        _MockStreamChunk(finish_reason=finish_reason, usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}),
    ]
    return _MockStream(chunks)


def _make_tool_call_stream(tool_name: str, arguments: dict[str, Any], call_id: str = "tc1") -> _MockStream:
    """Build a mock streaming response with tool_calls."""
    tc_delta = MagicMock()
    tc_delta.index = 0
    tc_delta.id = call_id
    func_delta = MagicMock()
    func_delta.name = tool_name
    func_delta.arguments = json.dumps(arguments)
    tc_delta.function = func_delta

    chunks = [
        _MockStreamChunk(tool_calls=[tc_delta]),
        _MockStreamChunk(finish_reason="tool_calls", usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}),
    ]
    return _MockStream(chunks)


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
        mock_litellm.acompletion = AsyncMock(return_value=_make_stream("Hello!"))

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
        mock_litellm.acompletion = AsyncMock(return_value=_make_stream("ok"))
        mock_litellm.cost_per_token = MagicMock(return_value=(0.001, 0.002))

        executor = LiteLLMExecutor()
        result = await executor.execute(
            messages=[{"role": "user", "content": "test"}],
            tools=None,
            tool_handler=None,
            model="test-model",
            api_key="key",
        )

        assert result.cost_usd == 0.003  # 0.001 + 0.002


class TestLiteLLMExecutorWithTools:
    """Test the agentic loop with tool calls."""

    @pytest.mark.asyncio
    @patch("backend.src.core.executor.litellm_executor.litellm")
    async def test_single_tool_call_then_response(self, mock_litellm: MagicMock, ctx: ToolContext) -> None:
        """LLM calls a tool, gets result, then responds."""
        tool_stream = _make_tool_call_stream("echo", {"text": "hi"})
        final_stream = _make_stream("Tool said: echoed: hi")
        mock_litellm.acompletion = AsyncMock(side_effect=[tool_stream, final_stream])

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
        tc1 = _make_tool_call_stream("echo", {"text": "a"}, "tc1")
        tc2 = _make_tool_call_stream("echo", {"text": "b"}, "tc2")
        final = _make_stream("Done with both")
        mock_litellm.acompletion = AsyncMock(side_effect=[tc1, tc2, final])

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
        mock_litellm.acompletion = AsyncMock(
            side_effect=lambda **kw: _make_tool_call_stream("echo", {"text": "loop"})
        )

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
        tool_stream = _make_tool_call_stream("echo", {"text": "hi"})
        final_stream = _make_stream("done")
        mock_litellm.acompletion = AsyncMock(side_effect=[tool_stream, final_stream])

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

    @pytest.mark.asyncio
    @patch("backend.src.core.executor.litellm_executor.litellm")
    async def test_tool_call_promoted_from_content_leak(
        self, mock_litellm: MagicMock, ctx: ToolContext
    ) -> None:
        """Qwen3 tool_call text leak: bare JSON in content is promoted to tool_calls."""
        leak_stream = _MockStream([
            _MockStreamChunk(content='{"name": "echo", "arguments": {"text": "hi"}}'),
            _MockStreamChunk(
                finish_reason="stop",
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            ),
        ])
        final_stream = _make_stream("Done")
        mock_litellm.acompletion = AsyncMock(side_effect=[leak_stream, final_stream])

        tool = EchoTool()
        handler = ToolHandler([tool], ctx)
        executor = LiteLLMExecutor()

        result = await executor.execute(
            messages=[{"role": "user", "content": "echo"}],
            tools=[tool.to_definition()],
            tool_handler=handler,
            model="ollama/qwen3-coder:30b",
            api_key="",
        )

        # The leaked JSON should be promoted to a tool call
        assert len(result.tool_calls_made) == 1
        assert result.tool_calls_made[0].name == "echo"
        assert result.tool_calls_made[0].input == {"text": "hi"}

    @pytest.mark.asyncio
    @patch("backend.src.core.executor.litellm_executor.litellm")
    async def test_status_update_event(self, mock_litellm: MagicMock) -> None:
        """STATUS marker in streamed text emits status_update event."""
        stream = _MockStream([
            _MockStreamChunk(content="시작합니다. "),
            _MockStreamChunk(content="[STATUS 시장 조사 분석 중]"),
            _MockStreamChunk(content=" 계속합니다."),
            _MockStreamChunk(finish_reason="stop", usage={"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10}),
        ])
        mock_litellm.acompletion = AsyncMock(return_value=stream)

        events: list = []
        executor = LiteLLMExecutor()
        result = await executor.execute(
            messages=[{"role": "user", "content": "test"}],
            tools=None,
            tool_handler=None,
            model="test-model",
            api_key="key",
            on_event=lambda e: events.append(e),
        )

        status_events = [e for e in events if e.type == "status_update"]
        assert len(status_events) == 1
        assert status_events[0].data["text"] == "시장 조사 분석 중"
        assert "시장 조사 분석 중" in result.content
