"""``DirectLLMAdapter`` — direct-LLM path with MCP tool loop.

Pin the contract: same ``execute(system_prompt, user_prompt, *,
tools_allowed, history)`` surface as :class:`BSGatewayAdapter`,
internally driving a litellm completion + MCP tool dispatch loop.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.src.core.llm.direct_client import (
    DirectLLMAdapter,
    DirectLLMError,
    _mcp_tool_to_openai,
    _serialise_tool_result,
)


def test_mcp_client_session_imports_from_sdk_not_internal_module() -> None:
    """The SDK's ``ClientSession`` must be importable from
    ``mcp.client.session``. ``backend/src/mcp/`` is BSNexus's own MCP
    *server* module; in some site-packages layouts (notably the
    production container) it shadows the top-level ``mcp`` package and
    breaks ``from mcp import ClientSession`` at runtime — surfaces only
    when a run actually has MCP servers configured. Pin the deep
    import path so a future contributor doesn't shorten it back.
    """
    from mcp.client.session import ClientSession  # noqa: PLC0415
    from mcp.client.streamable_http import streamablehttp_client  # noqa: PLC0415

    assert isinstance(ClientSession, type)
    assert callable(streamablehttp_client)


# ─── MCP ↔ OpenAI translation helpers ────────────────────────────────


def test_mcp_tool_to_openai_shape() -> None:
    tool = MagicMock(
        name="decision_create",
        description="Open a decision",
        inputSchema={
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
    )
    tool.name = "decision_create"  # MagicMock(name=...) is a separate kwarg
    out = _mcp_tool_to_openai(tool)
    assert out["type"] == "function"
    assert out["function"]["name"] == "decision_create"
    assert out["function"]["description"] == "Open a decision"
    assert out["function"]["parameters"]["properties"]["question"]["type"] == "string"


def test_mcp_tool_missing_schema_falls_back_to_empty_object() -> None:
    tool = MagicMock()
    tool.name = "noop"
    tool.description = ""
    tool.inputSchema = None
    out = _mcp_tool_to_openai(tool)
    assert out["function"]["parameters"] == {"type": "object", "properties": {}}


def test_serialise_tool_result_concats_text_blocks() -> None:
    block1 = MagicMock(text="line one")
    block2 = MagicMock(text="line two")
    result = MagicMock(content=[block1, block2])
    assert _serialise_tool_result(result) == "line one\nline two"


def test_serialise_tool_result_handles_non_text() -> None:
    block = MagicMock(spec=[])  # no .text attr
    result = MagicMock(content=[block])
    assert _serialise_tool_result(result) == "[non-text content omitted]"


def test_serialise_tool_result_empty_content() -> None:
    result = MagicMock(content=[])
    assert _serialise_tool_result(result) == "[empty]"


# ─── Stream consumer ─────────────────────────────────────────────────


def _delta_chunk(content: str = "", tool_calls: list[dict] | None = None, finish_reason: str | None = None) -> Any:
    """Build a litellm-style streaming chunk."""
    delta = MagicMock()
    delta.content = content if content else None
    if tool_calls:
        out = []
        for tc in tool_calls:
            mock_tc = MagicMock()
            mock_tc.index = tc.get("index", 0)
            mock_tc.id = tc.get("id")
            fn = MagicMock()
            fn.name = tc.get("function_name")
            fn.arguments = tc.get("function_arguments")
            mock_tc.function = fn
            out.append(mock_tc)
        delta.tool_calls = out
    else:
        delta.tool_calls = None
    choice = MagicMock()
    choice.delta = delta
    choice.finish_reason = finish_reason
    chunk = MagicMock()
    chunk.choices = [choice]
    return chunk


async def _async_iter(items: list[Any]):
    for item in items:
        yield item


@pytest.mark.asyncio
async def test_consume_stream_concatenates_content_and_invokes_on_chunk() -> None:
    received: list[str] = []

    async def on_chunk(text: str) -> None:
        received.append(text)

    adapter = DirectLLMAdapter(
        model="anthropic/claude-3-5-sonnet",
        api_key="k",
        project_id=uuid.uuid4(),
        on_chunk=on_chunk,
    )
    stream = _async_iter(
        [
            _delta_chunk(content="Hel"),
            _delta_chunk(content="lo "),
            _delta_chunk(content="world"),
            _delta_chunk(finish_reason="stop"),
        ]
    )
    parts, tool_calls, finish_reason = await adapter._consume_stream(stream)
    assert parts == ["Hel", "lo ", "world"]
    assert tool_calls == []
    assert finish_reason == "stop"
    assert received == ["Hel", "lo ", "world"]


@pytest.mark.asyncio
async def test_consume_stream_coalesces_tool_call_deltas() -> None:
    """Tool-call fragments accumulate across chunks per the OpenAI
    streaming spec (id arrives early, function.arguments builds up)."""
    adapter = DirectLLMAdapter(
        model="openai/gpt-4o",
        api_key="k",
        project_id=uuid.uuid4(),
    )
    stream = _async_iter(
        [
            _delta_chunk(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "call_1",
                        "function_name": "decision_create",
                        "function_arguments": '{"qu',
                    }
                ]
            ),
            _delta_chunk(
                tool_calls=[
                    {
                        "index": 0,
                        "function_arguments": 'estion": "OAuth or magic links?"}',
                    }
                ]
            ),
            _delta_chunk(finish_reason="tool_calls"),
        ]
    )
    parts, tool_calls, finish_reason = await adapter._consume_stream(stream)
    assert parts == []
    assert finish_reason == "tool_calls"
    assert len(tool_calls) == 1
    assert tool_calls[0]["id"] == "call_1"
    assert tool_calls[0]["function"]["name"] == "decision_create"
    assert tool_calls[0]["function"]["arguments"] == '{"question": "OAuth or magic links?"}'


# ─── Tool loop round-trip ────────────────────────────────────────────


def _build_session_mock(
    tools: list[Any] | None = None,
    call_results: dict[str, Any] | None = None,
) -> AsyncMock:
    """Mock ``mcp.ClientSession`` for the tool loop."""
    session = AsyncMock()
    list_result = MagicMock()
    list_result.tools = tools or []
    session.list_tools = AsyncMock(return_value=list_result)

    async def _call_tool(name: str, args: dict) -> Any:
        if call_results and name in call_results:
            return call_results[name]
        out = MagicMock()
        out.content = [MagicMock(text=f"result for {name}({args})")]
        return out

    session.call_tool = AsyncMock(side_effect=_call_tool)
    return session


@pytest.mark.asyncio
async def test_tool_loop_no_tool_calls_returns_text() -> None:
    """Model returns content + finish_reason=stop on first round →
    loop exits, executor result has the concatenated text."""
    adapter = DirectLLMAdapter(
        model="anthropic/claude-3-5-sonnet",
        api_key="k",
        project_id=uuid.uuid4(),
    )
    stream = _async_iter(
        [
            _delta_chunk(content="Done"),
            _delta_chunk(finish_reason="stop"),
        ]
    )
    with patch(
        "backend.src.core.llm.direct_client.acompletion",
        AsyncMock(return_value=stream),
    ):
        result = await adapter._tool_loop([], session=None, openai_tools=None)
    assert result["output_type"] == "text"
    assert result["output_ref"] == {"inline": "Done"}
    assert result["actual_cost_cents"] == 0
    assert result["finish_reason"] == "stop"
    # No MCP tools dispatched → empty activity log.
    assert result["tool_activity_log"] == []


@pytest.mark.asyncio
async def test_tool_loop_dispatches_tool_call_then_continues() -> None:
    """Round 1: tool_call → MCP dispatch → message appended.
    Round 2: model produces final text → loop exits."""
    adapter = DirectLLMAdapter(
        model="openai/gpt-4o",
        api_key="k",
        project_id=uuid.uuid4(),
    )
    session = _build_session_mock()

    round1 = _async_iter(
        [
            _delta_chunk(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "c1",
                        "function_name": "knowledge_search",
                        "function_arguments": '{"query": "x"}',
                    }
                ]
            ),
            _delta_chunk(finish_reason="tool_calls"),
        ]
    )
    round2 = _async_iter(
        [
            _delta_chunk(content="Got "),
            _delta_chunk(content="answer"),
            _delta_chunk(finish_reason="stop"),
        ]
    )

    with patch(
        "backend.src.core.llm.direct_client.acompletion",
        AsyncMock(side_effect=[round1, round2]),
    ):
        result = await adapter._tool_loop([], session=session, openai_tools=[])

    session.call_tool.assert_awaited_once_with("knowledge_search", {"query": "x"})
    assert result["output_ref"] == {"inline": "Got answer"}
    assert result["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_tool_loop_failed_tool_call_appends_error_message() -> None:
    """Tool dispatch exception → error JSON appended as tool message,
    loop continues so the model can recover."""
    adapter = DirectLLMAdapter(
        model="openai/gpt-4o",
        api_key="k",
        project_id=uuid.uuid4(),
    )
    session = AsyncMock()
    list_result = MagicMock()
    list_result.tools = []
    session.list_tools = AsyncMock(return_value=list_result)
    session.call_tool = AsyncMock(side_effect=RuntimeError("decision queue down"))

    captured_messages: list[list[dict]] = []
    round1 = _async_iter(
        [
            _delta_chunk(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "c1",
                        "function_name": "decision_wait",
                        "function_arguments": "{}",
                    }
                ]
            ),
            _delta_chunk(finish_reason="tool_calls"),
        ]
    )
    round2 = _async_iter(
        [
            _delta_chunk(content="Sorry, decisions unavailable"),
            _delta_chunk(finish_reason="stop"),
        ]
    )

    async def _fake_acompletion(**kwargs):
        captured_messages.append([dict(m) for m in kwargs["messages"]])
        return round1 if len(captured_messages) == 1 else round2

    with patch(
        "backend.src.core.llm.direct_client.acompletion",
        AsyncMock(side_effect=_fake_acompletion),
    ):
        result = await adapter._tool_loop([], session=session, openai_tools=[])

    # Round 2 should have seen the failed tool message in the history.
    round2_messages = captured_messages[1]
    tool_msg = [m for m in round2_messages if m.get("role") == "tool"]
    assert len(tool_msg) == 1
    assert "decision queue down" in tool_msg[0]["content"]
    assert result["output_ref"]["inline"].startswith("Sorry")


@pytest.mark.asyncio
async def test_tool_loop_round_cap_raises() -> None:
    """Runaway loop is capped — model that keeps requesting tools past
    ``max_tool_rounds`` raises ``DirectLLMError`` carrying partial output."""
    adapter = DirectLLMAdapter(
        model="openai/gpt-4o",
        api_key="k",
        project_id=uuid.uuid4(),
        max_tool_rounds=2,
    )
    session = _build_session_mock()

    def _make_round():
        return _async_iter(
            [
                _delta_chunk(
                    content="...",
                    tool_calls=[
                        {
                            "index": 0,
                            "id": "c",
                            "function_name": "x",
                            "function_arguments": "{}",
                        }
                    ],
                ),
                _delta_chunk(finish_reason="tool_calls"),
            ]
        )

    with patch(
        "backend.src.core.llm.direct_client.acompletion",
        AsyncMock(side_effect=[_make_round(), _make_round()]),
    ):
        with pytest.raises(DirectLLMError) as exc_info:
            await adapter._tool_loop([], session=session, openai_tools=[])

    assert "exceeded 2 rounds" in str(exc_info.value)
    assert exc_info.value.partial_output == "......"


# ─── execute() end-to-end with MCP session mock ─────────────────────


@pytest.mark.asyncio
async def test_api_base_forwarded_to_acompletion_when_set() -> None:
    """Local-LLM (ollama, vLLM) deployments need ``api_base`` plumbed
    through to litellm. Mac Mini production hits ollama via Tailscale
    IP, so the executor config carries ``cfg.base_url`` and the
    adapter must pass it to ``acompletion(api_base=...)``.
    """
    adapter = DirectLLMAdapter(
        model="ollama/qwen3-coder:30b",
        api_key="any",
        project_id=uuid.uuid4(),
        api_base="http://100.64.0.5:11434",
    )

    captured_kwargs: dict[str, Any] = {}
    stream = _async_iter(
        [
            _delta_chunk(content="hi"),
            _delta_chunk(finish_reason="stop"),
        ]
    )

    async def _fake_acompletion(**kwargs):
        captured_kwargs.update(kwargs)
        return stream

    @asynccontextmanager
    async def _no_session():
        yield None

    with (
        patch.object(adapter, "_mcp_session", _no_session),
        patch(
            "backend.src.core.llm.direct_client.acompletion",
            AsyncMock(side_effect=_fake_acompletion),
        ),
    ):
        await adapter.execute(
            system_prompt="s",
            user_prompt="u",
            tools_allowed=[],
        )

    assert captured_kwargs["api_base"] == "http://100.64.0.5:11434"


@pytest.mark.asyncio
async def test_api_base_omitted_when_unset() -> None:
    """When the executor config doesn't carry a ``base_url`` (the
    Anthropic / OpenAI cloud case), litellm falls back to provider
    defaults — we must not pass an empty/None ``api_base`` and confuse
    the SDK.
    """
    adapter = DirectLLMAdapter(
        model="anthropic/claude-3-5-sonnet",
        api_key="any",
        project_id=uuid.uuid4(),
    )

    captured_kwargs: dict[str, Any] = {}
    stream = _async_iter(
        [
            _delta_chunk(content="hi"),
            _delta_chunk(finish_reason="stop"),
        ]
    )

    async def _fake_acompletion(**kwargs):
        captured_kwargs.update(kwargs)
        return stream

    @asynccontextmanager
    async def _no_session():
        yield None

    with (
        patch.object(adapter, "_mcp_session", _no_session),
        patch(
            "backend.src.core.llm.direct_client.acompletion",
            AsyncMock(side_effect=_fake_acompletion),
        ),
    ):
        await adapter.execute(
            system_prompt="s",
            user_prompt="u",
            tools_allowed=[],
        )

    assert "api_base" not in captured_kwargs


@pytest.mark.asyncio
async def test_execute_assembles_messages_from_history() -> None:
    """``execute`` builds messages from system + filtered history + user."""
    adapter = DirectLLMAdapter(
        model="anthropic/claude-3-5-sonnet",
        api_key="k",
        project_id=uuid.uuid4(),
    )

    captured_messages: list[dict] = []
    stream = _async_iter(
        [
            _delta_chunk(content="ok"),
            _delta_chunk(finish_reason="stop"),
        ]
    )

    async def _fake_acompletion(**kwargs):
        captured_messages.extend(kwargs["messages"])
        return stream

    @asynccontextmanager
    async def _no_session():
        yield None

    with (
        patch.object(adapter, "_mcp_session", _no_session),
        patch(
            "backend.src.core.llm.direct_client.acompletion",
            AsyncMock(side_effect=_fake_acompletion),
        ),
    ):
        result = await adapter.execute(
            system_prompt="be helpful",
            user_prompt="say hi",
            tools_allowed=["file_read"],
            history=[
                {"role": "user", "content": "earlier"},
                {"role": "assistant", "content": "response"},
                {"role": "tool", "content": "should be filtered"},
            ],
        )

    assert captured_messages[0] == {"role": "system", "content": "be helpful"}
    assert captured_messages[1] == {"role": "user", "content": "earlier"}
    assert captured_messages[2] == {"role": "assistant", "content": "response"}
    assert captured_messages[3] == {"role": "user", "content": "say hi"}
    # tool-role history entries are filtered
    assert all(m["role"] != "tool" for m in captured_messages)
    assert result["output_ref"] == {"inline": "ok"}
