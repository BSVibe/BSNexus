"""Hang backstops for ``DirectLLMAdapter`` — instrumentation + timeouts.

Defends against the LLM tool-loop hang documented in
``docs/known-issues/llm-tool-loop-hang.md``: a run sits in ``running``
status indefinitely with CPU 0% and no outbound socket to Ollama.
The first occurrence (2026-04-25) bracketed the hang to "between
iteration 1's response handling and iteration 2's LLM call" — i.e.
either the tool dispatch path (``session.call_tool``) or the next
``acompletion`` call. There were no structured logs or per-call
timeouts to bracket which boundary, so debugging required killing
the process.

This module pins:

  1. Structured iteration / tool-call boundary logs so the last log
     line before a hang identifies the parked frame.
  2. Per-iteration ``acompletion`` timeout that fires as
     ``DirectLLMError`` instead of blocking the dispatch task forever.
  3. Per-tool-call ``session.call_tool`` timeout so a stalled MCP
     server can't sink the run.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.src.core.llm.direct_client import DirectLLMAdapter, DirectLLMError


# ── Helpers (small parallels of the existing test module) ─────────────


def _delta_chunk(
    content: str = "",
    tool_calls: list[dict] | None = None,
    finish_reason: str | None = None,
) -> Any:
    delta = MagicMock()
    delta.content = content or None
    if tool_calls:
        deltas = []
        for entry in tool_calls:
            mock_tc = MagicMock()
            mock_tc.index = entry.get("index", 0)
            mock_tc.id = entry.get("id")
            fn = MagicMock()
            fn.name = entry.get("function_name")
            fn.arguments = entry.get("function_arguments", "")
            mock_tc.function = fn
            deltas.append(mock_tc)
        delta.tool_calls = deltas
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


# ── 1. Structured iteration / tool-call logs ─────────────────────────


@pytest.mark.asyncio
async def test_iteration_start_and_returned_logs_emitted() -> None:
    """Each round logs ``llm_iteration_start`` before the
    ``acompletion`` call and ``llm_iteration_returned`` after the
    stream drains. The two-event bracket lets ops grep for hung
    iterations from production logs alone.
    """
    adapter = DirectLLMAdapter(
        model="openai/gpt-4o",
        api_key="k",
        project_id=uuid.uuid4(),
    )
    stream = _async_iter([_delta_chunk(content="ok"), _delta_chunk(finish_reason="stop")])

    captured: list[tuple[str, dict[str, Any]]] = []

    def _capture(event: str, **kwargs: Any) -> None:
        captured.append((event, kwargs))

    log = MagicMock()
    log.info.side_effect = lambda event, **kw: _capture(event, **kw)
    log.warning.side_effect = lambda event, **kw: _capture(event, **kw)

    with patch("backend.src.core.llm.direct_client.logger", log):
        with patch(
            "backend.src.core.llm.direct_client.acompletion",
            AsyncMock(return_value=stream),
        ):
            await adapter._tool_loop([], session=None, openai_tools=None)

    events = [name for name, _ in captured]
    assert "llm_iteration_start" in events
    assert "llm_iteration_returned" in events
    # Both events MUST carry the run/project context so a grep can
    # bracket which run hung even when many runs share the log file.
    start_kwargs = next(kw for name, kw in captured if name == "llm_iteration_start")
    assert "project_id" in start_kwargs
    assert "round_idx" in start_kwargs


@pytest.mark.asyncio
async def test_tool_call_start_and_done_logs_emitted() -> None:
    """``_dispatch_tool_call`` brackets each MCP call with start/done
    events so a hang on session.call_tool surfaces as the last line."""
    adapter = DirectLLMAdapter(
        model="openai/gpt-4o",
        api_key="k",
        project_id=uuid.uuid4(),
    )
    session = AsyncMock()
    out = MagicMock()
    out.content = [MagicMock(text="result")]
    session.call_tool = AsyncMock(return_value=out)

    captured: list[tuple[str, dict[str, Any]]] = []
    log = MagicMock()
    log.info.side_effect = lambda event, **kw: captured.append((event, kw))
    log.warning.side_effect = lambda event, **kw: captured.append((event, kw))

    with patch("backend.src.core.llm.direct_client.logger", log):
        await adapter._dispatch_tool_call(
            session,
            {
                "id": "c1",
                "type": "function",
                "function": {"name": "knowledge_search", "arguments": '{"q": "x"}'},
            },
        )

    events = [name for name, _ in captured]
    assert "tool_call_start" in events
    assert "tool_call_done" in events


# ── 2. Per-iteration acompletion timeout ─────────────────────────────


@pytest.mark.asyncio
async def test_iteration_timeout_raises_direct_llm_error() -> None:
    """``acompletion`` blocked indefinitely → ``DirectLLMError`` after
    the configured iteration timeout, not a wedged task.

    The fix is wrapping the call in ``asyncio.wait_for(..., timeout=
    iteration_timeout_s)``. Production default sits much higher; the
    test pins seconds.
    """
    adapter = DirectLLMAdapter(
        model="openai/gpt-4o",
        api_key="k",
        project_id=uuid.uuid4(),
        iteration_timeout_s=0.05,
    )

    async def _hang(**_: Any) -> Any:
        await asyncio.Event().wait()  # never returns

    with patch("backend.src.core.llm.direct_client.acompletion", _hang):
        with pytest.raises(DirectLLMError, match="iteration"):
            await adapter._tool_loop([], session=None, openai_tools=None)


@pytest.mark.asyncio
async def test_iteration_timeout_during_stream_consume_also_caught() -> None:
    """If the stream itself hangs mid-drain (litellm parked on an
    httpx future), the same timeout still fires — the wait_for has to
    wrap the whole iteration, not just the ``await acompletion(...)``
    call that returns the iterator."""
    adapter = DirectLLMAdapter(
        model="openai/gpt-4o",
        api_key="k",
        project_id=uuid.uuid4(),
        iteration_timeout_s=0.05,
    )

    async def _hanging_stream():
        yield _delta_chunk(content="partial ")
        await asyncio.Event().wait()  # hang mid-stream
        yield _delta_chunk(finish_reason="stop")

    with patch(
        "backend.src.core.llm.direct_client.acompletion",
        AsyncMock(return_value=_hanging_stream()),
    ):
        with pytest.raises(DirectLLMError, match="iteration"):
            await adapter._tool_loop([], session=None, openai_tools=None)


# ── 3. Per-tool-call timeout ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_tool_call_timeout_raises_partial_then_continues() -> None:
    """A hung MCP ``call_tool`` does not block the loop indefinitely.

    ``_dispatch_tool_call`` wraps ``session.call_tool`` in
    ``asyncio.wait_for(..., timeout=tool_call_timeout_s)``; on timeout
    the helper appends an error tool message so the model can recover
    on the next round (mirrors the existing ``RuntimeError`` recovery
    branch — symmetry kept on purpose)."""
    adapter = DirectLLMAdapter(
        model="openai/gpt-4o",
        api_key="k",
        project_id=uuid.uuid4(),
        tool_call_timeout_s=0.05,
    )
    session = AsyncMock()

    async def _hang(*_: Any, **__: Any) -> Any:
        await asyncio.Event().wait()

    session.call_tool = _hang

    msg = await adapter._dispatch_tool_call(
        session,
        {
            "id": "c1",
            "type": "function",
            "function": {"name": "decision_wait", "arguments": "{}"},
        },
    )

    assert msg["role"] == "tool"
    assert msg["tool_call_id"] == "c1"
    assert "timeout" in msg["content"].lower()


@pytest.mark.asyncio
async def test_tool_call_timeout_default_value_lock() -> None:
    """Per-call timeout default ≥ shell_exec ceiling (180s) so we
    don't pre-empt long-but-legitimate file_write / shell_exec calls
    that the BSNexus MCP server proxies through. Production tunes
    higher; the floor is what's pinned here."""
    adapter = DirectLLMAdapter(
        model="openai/gpt-4o",
        api_key="k",
        project_id=uuid.uuid4(),
    )

    assert adapter._tool_call_timeout_s >= 180.0


@pytest.mark.asyncio
async def test_iteration_timeout_default_value_lock() -> None:
    """Per-iteration timeout default ≥ 600s — matches the litellm
    ``timeout=`` knob the legacy adapter set, plus headroom for slow
    local LLMs. Lower would false-positive on big prompts."""
    adapter = DirectLLMAdapter(
        model="openai/gpt-4o",
        api_key="k",
        project_id=uuid.uuid4(),
    )

    assert adapter._iteration_timeout_s >= 600.0
