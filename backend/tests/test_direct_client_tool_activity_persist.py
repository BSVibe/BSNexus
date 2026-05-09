"""Pin: ``DirectLLMAdapter.execute()`` accumulates a structured
``tool_activity_log`` in its result so the dispatcher can persist them
as ``ExecutionRunActivity(level=tool)`` rows after the LLM call
finishes (PR7 — failure-mode instrumentation).

The adapter must NOT hold a DB session across the multi-minute LLM
call (dispatcher Phase 2 explicitly drops the session before
``execute()``). The contract is therefore in-memory accumulation +
return-by-value; persistence is the dispatcher's job.
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


def _session_mock(tool_results: dict[str, Any] | None = None) -> AsyncMock:
    session = AsyncMock()
    list_result = MagicMock()
    list_result.tools = []
    session.list_tools = AsyncMock(return_value=list_result)

    async def _call_tool(name: str, args: dict) -> Any:
        if tool_results and name in tool_results:
            r = tool_results[name]
            if isinstance(r, Exception):
                raise r
            return r
        out = MagicMock()
        out.content = [MagicMock(text=f"ran {name}({args})")]
        return out

    session.call_tool = AsyncMock(side_effect=_call_tool)
    return session


@pytest.mark.asyncio
async def test_execute_records_paired_tool_call_start_done_in_activity_log() -> None:
    """A run with one MCP tool call → ``tool_activity_log`` carries a
    paired ``tool_call_start`` + ``tool_call_done`` record with the
    expected keys."""
    adapter = DirectLLMAdapter(
        model="openai/gpt-4o",
        api_key="k",
        project_id=uuid.uuid4(),
    )

    session = _session_mock()
    round1 = _async_iter(
        [
            _delta_chunk(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "call_42",
                        "function_name": "file_write",
                        "function_arguments": '{"path": "add.py", "content": "def add(a,b): return a+b\\n"}',
                    }
                ]
            ),
            _delta_chunk(finish_reason="tool_calls"),
        ]
    )
    # PR9 — round 2 includes the fenced block to short-circuit the
    # block-forgotten watchdog.
    round2 = _async_iter(
        [
            _delta_chunk(content="done"),
            _delta_chunk(content='\n```bsnexus-verification\n{"verifier_type": "software_test", "command": ["true"]}\n```'),
            _delta_chunk(finish_reason="stop"),
        ]
    )

    @asynccontextmanager
    async def _session_ctx():
        yield session

    with (
        patch.object(adapter, "_mcp_session", _session_ctx),
        patch.object(adapter, "_fetch_openai_tools", AsyncMock(return_value=[])),
        patch(
            "backend.src.core.llm.direct_client.acompletion",
            AsyncMock(side_effect=[round1, round2]),
        ),
    ):
        result = await adapter.execute(
            system_prompt="be helpful",
            user_prompt="write add.py",
            tools_allowed=[],
        )

    log = result["tool_activity_log"]
    starts = [r for r in log if r["kind"] == "tool_call_start"]
    dones = [r for r in log if r["kind"] == "tool_call_done"]

    assert len(starts) == 1, f"expected 1 start record, got {len(starts)}: {log}"
    assert len(dones) == 1, f"expected 1 done record, got {len(dones)}: {log}"

    s = starts[0]
    assert s["tool_name"] == "file_write"
    assert s["tool_call_id"] == "call_42"
    assert s["round_idx"] == 0
    assert "args" in s  # truncated args present
    assert "occurred_at" in s

    d = dones[0]
    assert d["tool_name"] == "file_write"
    assert d["tool_call_id"] == "call_42"
    assert d["round_idx"] == 0
    assert d["outcome"] == "ok"
    assert isinstance(d["duration_ms"], int)
    assert d["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_execute_records_done_outcome_error_when_tool_raises() -> None:
    """Tool dispatch raises → done record carries ``outcome="error"``
    and an ``error`` field with the exception message."""
    adapter = DirectLLMAdapter(
        model="openai/gpt-4o",
        api_key="k",
        project_id=uuid.uuid4(),
    )
    session = _session_mock(tool_results={"file_read": RuntimeError("vault down")})
    round1 = _async_iter(
        [
            _delta_chunk(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "c1",
                        "function_name": "file_read",
                        "function_arguments": '{"path": "x"}',
                    }
                ]
            ),
            _delta_chunk(finish_reason="tool_calls"),
        ]
    )
    # PR9 — round 2 includes the fenced block to short-circuit the
    # block-forgotten watchdog (else a 3rd round would fire).
    round2 = _async_iter(
        [
            _delta_chunk(content="recovered"),
            _delta_chunk(content='\n```bsnexus-verification\n{"verifier_type": "software_test", "command": ["true"]}\n```'),
            _delta_chunk(finish_reason="stop"),
        ]
    )

    @asynccontextmanager
    async def _session_ctx():
        yield session

    with (
        patch.object(adapter, "_mcp_session", _session_ctx),
        patch.object(adapter, "_fetch_openai_tools", AsyncMock(return_value=[])),
        patch(
            "backend.src.core.llm.direct_client.acompletion",
            AsyncMock(side_effect=[round1, round2]),
        ),
    ):
        result = await adapter.execute(
            system_prompt="s",
            user_prompt="u",
            tools_allowed=[],
        )

    dones = [r for r in result["tool_activity_log"] if r["kind"] == "tool_call_done"]
    assert len(dones) == 1
    assert dones[0]["outcome"] == "error"
    assert "vault down" in dones[0]["error"]


@pytest.mark.asyncio
async def test_execute_returns_empty_log_when_no_tool_calls() -> None:
    """Plain text run with zero tool calls → no tool_call records in
    the log (only the per-round llm_round_complete milestone). The
    list is always present so the dispatcher can iterate freely."""
    adapter = DirectLLMAdapter(
        model="anthropic/claude-3-5-sonnet",
        api_key="k",
        project_id=uuid.uuid4(),
    )
    stream = _async_iter(
        [
            _delta_chunk(content="hi"),
            _delta_chunk(finish_reason="stop"),
        ]
    )

    @asynccontextmanager
    async def _no_session():
        yield None

    with (
        patch.object(adapter, "_mcp_session", _no_session),
        patch(
            "backend.src.core.llm.direct_client.acompletion",
            AsyncMock(side_effect=[stream]),
        ),
    ):
        result = await adapter.execute(
            system_prompt="s",
            user_prompt="u",
            tools_allowed=[],
        )

    log = result["tool_activity_log"]
    tool_records = [r for r in log if r["kind"].startswith("tool_call_")]
    assert tool_records == []


@pytest.mark.asyncio
async def test_execute_truncates_long_tool_args_in_activity_log() -> None:
    """A 10KB args string (e.g. file_write of a long file) is truncated
    in the activity log so the row stays under a sensible JSONB cap."""
    adapter = DirectLLMAdapter(
        model="openai/gpt-4o",
        api_key="k",
        project_id=uuid.uuid4(),
    )
    huge = "x" * 10_000
    session = _session_mock()
    round1 = _async_iter(
        [
            _delta_chunk(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "c1",
                        "function_name": "file_write",
                        "function_arguments": '{"content": "' + huge + '"}',
                    }
                ]
            ),
            _delta_chunk(finish_reason="tool_calls"),
        ]
    )
    # PR9 — round 2 includes the fenced block to short-circuit the
    # block-forgotten watchdog.
    round2 = _async_iter(
        [
            _delta_chunk(content="ok"),
            _delta_chunk(content='\n```bsnexus-verification\n{"verifier_type": "software_test", "command": ["true"]}\n```'),
            _delta_chunk(finish_reason="stop"),
        ]
    )

    @asynccontextmanager
    async def _session_ctx():
        yield session

    with (
        patch.object(adapter, "_mcp_session", _session_ctx),
        patch.object(adapter, "_fetch_openai_tools", AsyncMock(return_value=[])),
        patch(
            "backend.src.core.llm.direct_client.acompletion",
            AsyncMock(side_effect=[round1, round2]),
        ),
    ):
        result = await adapter.execute(
            system_prompt="s",
            user_prompt="u",
            tools_allowed=[],
        )

    starts = [r for r in result["tool_activity_log"] if r["kind"] == "tool_call_start"]
    assert len(starts) == 1
    assert len(starts[0]["args"]) <= 1024  # 1KB cap (per task description)
