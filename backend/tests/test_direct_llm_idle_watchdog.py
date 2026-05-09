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
    # PR9 multi-fire watchdog — round 3 must include the fenced block
    # to satisfy the watchdog and exit the loop. Otherwise the
    # block-forgotten variant fires again after round 3.
    round3 = _async_iter(
        [
            _delta_chunk(content='Done.\n```bsnexus-verification\n{"verifier_type": "software_test", "command": ["true"]}\n```'),
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
async def test_watchdog_does_not_fire_on_healthy_run_with_block() -> None:
    """Round 1 tool_call → round 2 completes WITH the fenced block.
    No nudge — the run produced everything it needed."""
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
            _delta_chunk(
                content='Done.\n```bsnexus-verification\n{"verifier_type": "software_test", "command": ["true"]}\n```',
            ),
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

    # Two rounds, no nudge.
    assert len(captured_messages) == 2
    nudges = [r for r in adapter._tool_activity_log if r.get("kind") == "tool_loop_watchdog_nudged"]
    assert nudges == []


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
async def test_watchdog_fires_when_block_missing_after_tool_calls() -> None:
    """PR9 iter-2 observation: LLM completes the work (file_write × 2,
    shell_exec × 1) but the final round emits prose only — no fenced
    block. Watchdog injects nudge; final round emits the block."""
    adapter = DirectLLMAdapter(
        model="ollama_chat/qwen3-coder:30b",
        api_key="k",
        project_id=uuid.uuid4(),
    )
    session = _session_with_tools()
    captured_messages: list[list[dict]] = []

    # Round 1: tool call → MCP dispatch
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
    # Round 2: prose only, NO fenced block (the bug)
    round2 = _async_iter(
        [
            _delta_chunk(content="All done! Tests pass."),
            _delta_chunk(finish_reason="stop"),
        ]
    )
    # Round 3 (after watchdog nudge): the fenced block
    round3 = _async_iter(
        [
            _delta_chunk(
                content='```bsnexus-verification\n{"verifier_type": "software_test", "command": ["true"]}\n```',
            ),
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
        await adapter.execute(system_prompt="s", user_prompt="u", tools_allowed=[])

    # Three rounds. Round 3 messages must include the watchdog nudge.
    assert len(captured_messages) == 3
    round3_msgs = captured_messages[2]
    nudge_msgs = [
        m
        for m in round3_msgs
        if m.get("role") == "system" and "bsnexus-verification" in (m.get("content") or "")
    ]
    assert len(nudge_msgs) == 1
    nudges = [r for r in adapter._tool_activity_log if r.get("kind") == "tool_loop_watchdog_nudged"]
    assert len(nudges) == 1


@pytest.mark.asyncio
async def test_watchdog_caps_at_max_fires_per_run() -> None:
    """PR9 iter 3 observed qwen3-coder responding to a single watchdog
    nudge with more tool calls instead of the requested block. Allow
    the watchdog to fire up to ``_WATCHDOG_MAX_FIRES`` times so we can
    re-nudge after each subsequent quiet round, then break to avoid
    runaway nudge loops."""
    from backend.src.core.llm.direct_client import _WATCHDOG_MAX_FIRES

    adapter = DirectLLMAdapter(
        model="ollama_chat/qwen3-coder:30b",
        api_key="k",
        project_id=uuid.uuid4(),
    )
    session = _session_with_tools()
    captured_messages: list[list[dict]] = []

    # Five rounds of preamble-only — model never emits the block. The
    # watchdog should fire ``_WATCHDOG_MAX_FIRES`` times then give up.
    rounds = [
        _async_iter([_delta_chunk(content=f"thought {i}"), _delta_chunk(finish_reason="stop")])
        for i in range(_WATCHDOG_MAX_FIRES + 2)
    ]

    async def _fake_acompletion(**kwargs):
        captured_messages.append([dict(m) for m in kwargs["messages"]])
        idx = len(captured_messages)
        return rounds[idx - 1]

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

    # ``_WATCHDOG_MAX_FIRES`` nudges + 1 final round that breaks the loop.
    assert len(captured_messages) == _WATCHDOG_MAX_FIRES + 1
    nudges_in_log = [r for r in adapter._tool_activity_log if r.get("kind") == "tool_loop_watchdog_nudged"]
    assert len(nudges_in_log) == _WATCHDOG_MAX_FIRES
