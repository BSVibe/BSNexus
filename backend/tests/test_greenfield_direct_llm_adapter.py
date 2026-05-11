"""Tests for ``core.llm.DirectLLMAdapter`` (G6.2 — Piece 2).

litellm direct path for ``executor_type=llm_api``. Mirrors the
``BSGatewayClient.execute()`` contract so the resolver returned client
is interchangeable from the caller's POV (G6.3 RunAttempt executor).

litellm itself is mocked here — the adapter is a thin async wrapper
around ``litellm.acompletion(..., stream=True)`` and the test focuses
on the wire shape, the on_chunk callback, and the result dict.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest


class _FakeStreamChunk:
    """Mimic the minimal litellm streaming chunk shape: ``choices[0].delta.content``
    plus an optional ``finish_reason``."""

    def __init__(self, content: str | None, finish_reason: str | None = None) -> None:
        self.choices = [_FakeStreamChoice(content, finish_reason)]


class _FakeStreamChoice:
    def __init__(self, content: str | None, finish_reason: str | None) -> None:
        self.delta = _FakeDelta(content)
        self.finish_reason = finish_reason


class _FakeDelta:
    def __init__(self, content: str | None) -> None:
        self.content = content


async def _fake_stream(chunks: list[_FakeStreamChunk]) -> AsyncIterator[_FakeStreamChunk]:
    for chunk in chunks:
        yield chunk


@pytest.mark.asyncio
async def test_direct_llm_adapter_returns_concatenated_text(monkeypatch):
    """The adapter should iterate the litellm stream, collect text
    deltas, and return the BSGateway-style result dict so callers can
    treat both paths identically."""
    from backend.src.core.llm import DirectLLMAdapter

    chunks = [
        _FakeStreamChunk("hello "),
        _FakeStreamChunk("world"),
        _FakeStreamChunk(None, finish_reason="stop"),
    ]

    captured: dict[str, Any] = {}

    async def fake_acompletion(**kwargs: Any) -> AsyncIterator[_FakeStreamChunk]:
        captured.update(kwargs)
        return _fake_stream(chunks)

    monkeypatch.setattr("backend.src.core.llm.direct_client.acompletion", fake_acompletion)

    adapter = DirectLLMAdapter(base_url="http://host.docker.internal:11434", api_key="sk-x")
    result = await adapter.execute(
        messages=[{"role": "user", "content": "hi"}],
        metadata={},
        model="ollama_chat/qwen3-coder:30b",
    )

    assert result["output_type"] == "text"
    assert result["output_ref"] == "hello world"
    assert result["finish_reason"] == "stop"
    # ``actual_cost_cents`` is 0 — BSNexus doesn't price the call here.
    assert result["actual_cost_cents"] == 0
    # litellm received the model + messages + stream flag + api_base
    # (we use the per-tenant ``base_url`` so a self-host Ollama hits
    # localhost, not the upstream provider).
    assert captured["model"] == "ollama_chat/qwen3-coder:30b"
    assert captured["messages"] == [{"role": "user", "content": "hi"}]
    assert captured["stream"] is True
    assert captured["api_base"] == "http://host.docker.internal:11434"
    assert captured["api_key"] == "sk-x"


@pytest.mark.asyncio
async def test_direct_llm_adapter_invokes_on_chunk_callback(monkeypatch):
    """The orchestrator (G6.3) wires ``on_chunk`` to fan SSE deltas
    onto the project stream so the founder sees the model thinking in
    real time. Without this, the LLM output appears in one blob at the
    end of the call."""
    from backend.src.core.llm import DirectLLMAdapter

    chunks = [
        _FakeStreamChunk("first "),
        _FakeStreamChunk("second"),
        _FakeStreamChunk(None, finish_reason="stop"),
    ]

    async def fake_acompletion(**_kwargs: Any) -> AsyncIterator[_FakeStreamChunk]:
        return _fake_stream(chunks)

    monkeypatch.setattr("backend.src.core.llm.direct_client.acompletion", fake_acompletion)

    streamed: list[str] = []

    async def on_chunk(delta: str) -> None:
        streamed.append(delta)

    adapter = DirectLLMAdapter(base_url=None, api_key="sk-x")
    await adapter.execute(
        messages=[{"role": "user", "content": "hi"}],
        metadata={},
        model="gpt-4o",
        on_chunk=on_chunk,
    )

    assert streamed == ["first ", "second"]


@pytest.mark.asyncio
async def test_direct_llm_adapter_raises_on_litellm_error(monkeypatch):
    """A failing acompletion raises ``DirectLLMError`` with the partial
    text accumulated so far — mirrors ``BSGatewayError.partial_output``
    so the orchestrator can surface what the model produced before the
    failure."""
    from backend.src.core.llm import DirectLLMAdapter, DirectLLMError

    chunks = [
        _FakeStreamChunk("partial "),
        _FakeStreamChunk("text "),
    ]

    async def fake_stream_then_fail() -> AsyncIterator[_FakeStreamChunk]:
        for chunk in chunks:
            yield chunk
        raise RuntimeError("upstream timeout")

    async def fake_acompletion(**_kwargs: Any) -> AsyncIterator[_FakeStreamChunk]:
        return fake_stream_then_fail()

    monkeypatch.setattr("backend.src.core.llm.direct_client.acompletion", fake_acompletion)

    adapter = DirectLLMAdapter(base_url=None, api_key="sk-x")
    with pytest.raises(DirectLLMError) as exc_info:
        await adapter.execute(
            messages=[{"role": "user", "content": "hi"}],
            metadata={},
            model="gpt-4o",
        )

    assert exc_info.value.partial_output == "partial text "


@pytest.mark.asyncio
async def test_direct_llm_adapter_omits_api_base_when_unset(monkeypatch):
    """SaaS providers (OpenAI / Anthropic) infer the endpoint from the
    model id; we must not force ``api_base=None`` into the call because
    litellm treats that as ``api_base=""`` and skips its built-in
    routing. Omit the kwarg entirely when ``base_url`` is None."""
    from backend.src.core.llm import DirectLLMAdapter

    captured: dict[str, Any] = {}

    async def fake_acompletion(**kwargs: Any) -> AsyncIterator[_FakeStreamChunk]:
        captured.update(kwargs)
        return _fake_stream([_FakeStreamChunk(None, finish_reason="stop")])

    monkeypatch.setattr("backend.src.core.llm.direct_client.acompletion", fake_acompletion)

    adapter = DirectLLMAdapter(base_url=None, api_key="sk-x")
    await adapter.execute(
        messages=[{"role": "user", "content": "hi"}],
        metadata={},
        model="gpt-4o",
    )

    assert "api_base" not in captured
