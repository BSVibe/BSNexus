"""PR9 — tool-loop idle watchdog: nudge the LLM when round 1 produces
zero tool calls AND no fenced verification block.

PR8 baseline observation: qwen3-coder under VRAM contention sometimes
emits a single preamble-only round ("I need to build a FastAPI app
with a test.") with zero tool calls and no verification block, then
exits. The deliverable lands at ``verification_missing`` and the run
"completes" without doing the work.

The watchdog injects a system nudge after such a round 1 — "you
stopped before doing the work; please proceed with the file_writes
and shell_exec, then emit the verification block" — and lets the
loop continue. Fires at most once per run (no infinite nudge).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.src.core.llm.direct_client import DirectLLMAdapter


def _delta_chunk(content: str = "", tool_calls: list[dict] | None = None, finish_reason: str | None = None) -> Any:
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


def _session_with_tools() -> AsyncMock:
    session = AsyncMock()
    list_result = MagicMock()
    list_result.tools = []  # tool list shape doesn't matter for this test
    session.list_tools = AsyncMock(return_value=list_result)

    async def _call_tool(name: str, args: dict) -> Any:
        out = MagicMock()
        out.content = [MagicMock(text=f"ran {name}")]
        return out

    session.call_tool = AsyncMock(side_effect=_call_tool)
    return session


@pytest.mark.asyncio
async def test_watchdog_nudges_after_round1_zero_tools_no_block() -> None:
    """Round 1: preamble only (no tools, no block) → watchdog injects
    a system nudge and the loop continues. Round 2: model uses a
    tool → loop completes successfully."""
    adapter = DirectLLMAdapter(
        model="ollama_chat/qwen3-coder:30b",
        api_key="k",
        project_id=uuid.uuid4(),
    )
    session = _session_with_tools()
    captured_messages: list[list[dict]] = []

    round1 = _async_iter(
        [
            _delta_chunk(content="I need to build the app."),
            _delta_chunk(finish_reason="stop"),
        ]
    )
    round2 = _async_iter(
        [
            _delta_chunk(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "c1",
                        "function_name": "file_write",
                        "function_arguments": '{"path": "x"}',
                    }
                ]
            ),
            _delta_chunk(finish_reason="tool_calls"),
        ]
    )
    round3 = _async_iter(
        [
            _delta_chunk(content="Done."),
            _delta_chunk(finish_reason="stop"),
        ]
    )

    async def _fake_acompletion(**kwargs):
        captured_messages.append([dict(m) for m in kwargs["messages"]])
        idx = len(captured_messages)
        return [round1, round2, round3][idx - 1]

    @asynccontextmanager
    async def _session_ctx():
        yield session

    with (
        patch.object(adapter, "_mcp_session", _session_ctx),
        patch.object(adapter, "_fetch_openai_tools", AsyncMock(return_value=[{"name": "file_write"}])),
        patch(
            "backend.src.core.llm.direct_client.acompletion",
            AsyncMock(side_effect=_fake_acompletion),
        ),
    ):
        result = await adapter.execute(system_prompt="s", user_prompt="u", tools_allowed=[])

    # Three rounds happened (watchdog injected a nudge between 1 and 2).
    assert len(captured_messages) == 3
    # The round-2 messages must include the watchdog nudge appended
    # after round-1 assistant message.
    round2_msgs = captured_messages[1]
    nudge_msgs = [
        m for m in round2_msgs if m.get("role") == "system" and "verification" in (m.get("content") or "").lower()
    ]
    assert len(nudge_msgs) >= 1, f"expected a watchdog nudge in round 2 messages, got {round2_msgs}"
    # And the run completes successfully.
    assert result["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_watchdog_does_not_fire_when_round1_emits_tool_call() -> None:
    """Round 1 with at least one tool_call is healthy — no nudge."""
    adapter = DirectLLMAdapter(
        model="ollama_chat/qwen3-coder:30b",
        api_key="k",
        project_id=uuid.uuid4(),
    )
    session = _session_with_tools()
    captured_messages: list[list[dict]] = []

    round1 = _async_iter(
        [
            _delta_chunk(
                tool_calls=[
                    {"index": 0, "id": "c1", "function_name": "file_write", "function_arguments": '{"path": "x"}'}
                ]
            ),
            _delta_chunk(finish_reason="tool_calls"),
        ]
    )
    round2 = _async_iter(
        [
            _delta_chunk(content="Done."),
            _delta_chunk(finish_reason="stop"),
        ]
    )

    async def _fake_acompletion(**kwargs):
        captured_messages.append([dict(m) for m in kwargs["messages"]])
        idx = len(captured_messages)
        return [round1, round2][idx - 1]

    @asynccontextmanager
    async def _session_ctx():
        yield session

    with (
        patch.object(adapter, "_mcp_session", _session_ctx),
        patch.object(adapter, "_fetch_openai_tools", AsyncMock(return_value=[{"name": "file_write"}])),
        patch(
            "backend.src.core.llm.direct_client.acompletion",
            AsyncMock(side_effect=_fake_acompletion),
        ),
    ):
        await adapter.execute(system_prompt="s", user_prompt="u", tools_allowed=[])

    # Two rounds, no nudge injected.
    assert len(captured_messages) == 2
    round2_msgs = captured_messages[1]
    nudge_msgs = [
        m for m in round2_msgs if m.get("role") == "system" and "you stopped" in (m.get("content") or "").lower()
    ]
    assert nudge_msgs == []


@pytest.mark.asyncio
async def test_watchdog_does_not_fire_when_round1_emits_fenced_block() -> None:
    """Round 1 with no tools but a fenced verification block is the
    smoke pattern — no nudge."""
    adapter = DirectLLMAdapter(
        model="ollama_chat/qwen3-coder:30b",
        api_key="k",
        project_id=uuid.uuid4(),
    )
    session = _session_with_tools()

    round1 = _async_iter(
        [
            _delta_chunk(
                content='```bsnexus-verification\n{"verifier_type": "software_test", "command": ["true"]}\n```',
            ),
            _delta_chunk(finish_reason="stop"),
        ]
    )
    captured_messages: list[list[dict]] = []

    async def _fake_acompletion(**kwargs):
        captured_messages.append([dict(m) for m in kwargs["messages"]])
        return round1

    @asynccontextmanager
    async def _session_ctx():
        yield session

    with (
        patch.object(adapter, "_mcp_session", _session_ctx),
        patch.object(adapter, "_fetch_openai_tools", AsyncMock(return_value=[{"name": "file_write"}])),
        patch(
            "backend.src.core.llm.direct_client.acompletion",
            AsyncMock(side_effect=_fake_acompletion),
        ),
    ):
        await adapter.execute(system_prompt="s", user_prompt="u", tools_allowed=[])

    # One round, finished cleanly.
    assert len(captured_messages) == 1


@pytest.mark.asyncio
async def test_watchdog_fires_at_most_once_per_run() -> None:
    """If round 1 AND round 2 both emit zero tools / no block, the
    watchdog fires only after round 1 — round 2 still triggers the
    normal early-break since tool_calls is empty."""
    adapter = DirectLLMAdapter(
        model="ollama_chat/qwen3-coder:30b",
        api_key="k",
        project_id=uuid.uuid4(),
    )
    session = _session_with_tools()
    captured_messages: list[list[dict]] = []

    round1 = _async_iter([_delta_chunk(content="I'll do it."), _delta_chunk(finish_reason="stop")])
    round2 = _async_iter([_delta_chunk(content="Still thinking."), _delta_chunk(finish_reason="stop")])

    async def _fake_acompletion(**kwargs):
        captured_messages.append([dict(m) for m in kwargs["messages"]])
        idx = len(captured_messages)
        return [round1, round2][idx - 1]

    @asynccontextmanager
    async def _session_ctx():
        yield session

    with (
        patch.object(adapter, "_mcp_session", _session_ctx),
        patch.object(adapter, "_fetch_openai_tools", AsyncMock(return_value=[{"name": "file_write"}])),
        patch(
            "backend.src.core.llm.direct_client.acompletion",
            AsyncMock(side_effect=_fake_acompletion),
        ),
    ):
        await adapter.execute(system_prompt="s", user_prompt="u", tools_allowed=[])

    # Exactly two rounds — watchdog fired once between them, then
    # round 2 also emitted no tools so the loop broke without a
    # second nudge.
    assert len(captured_messages) == 2
    # The activity log should record exactly one watchdog event.
    nudges_in_log = [r for r in adapter._tool_activity_log if r.get("kind") == "tool_loop_watchdog_nudged"]
    assert len(nudges_in_log) == 1
