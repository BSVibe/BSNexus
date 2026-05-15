"""Tests for ``core.llm.DirectLLMAdapter`` (G6.2 — Piece 2).

The adapter wraps :class:`bsvibe_llm.LlmClient` (``direct=True``) so
the shared retry / fallback / reasoning-suppression / wire-contract
plumbing lives in one place across all BSVibe products. BSNexus does
not import ``litellm`` directly anywhere — the fence is enforced by
``test_litellm_is_fenced_to_core_llm`` in the legacy-erasure suite.

Both ``BSGatewayClient`` and ``DirectLLMAdapter`` satisfy the
``ExecutorClient`` Protocol; the resolver returns the Protocol type so
callers never branch on the concrete class.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from bsvibe_llm import CompletionResult, LlmClient

from backend.src.core.executor_config import ExecutorClient
from backend.src.core.llm import DirectLLMAdapter, DirectLLMError


def _stub_client(result: CompletionResult | Exception) -> LlmClient:
    """Build an ``LlmClient`` whose ``.complete()`` returns a fixed
    ``CompletionResult`` (or raises). Avoids touching any real
    ``litellm`` / network surface from the test."""
    client = LlmClient.__new__(LlmClient)
    if isinstance(result, Exception):
        client.complete = AsyncMock(side_effect=result)  # type: ignore[method-assign]
    else:
        client.complete = AsyncMock(return_value=result)  # type: ignore[method-assign]
    return client


def _completion(text: str, finish_reason: str = "stop") -> CompletionResult:
    return CompletionResult(
        text=text,
        model="ollama_chat/qwen3-coder:30b",
        finish_reason=finish_reason,
        prompt_tokens=0,
        completion_tokens=0,
    )


@pytest.mark.asyncio
async def test_direct_adapter_returns_shared_executor_result_shape():
    """The adapter result must match the same ``execute()`` contract
    BSGatewayClient returns so downstream callers (G6.3 RunAttempt
    executor, G6.4 M0 harness bridge) don't branch."""
    stub = _stub_client(_completion("hello world"))
    adapter = DirectLLMAdapter(
        base_url="http://localhost:11434",
        api_key="sk-x",
        client=stub,
    )

    result = await adapter.execute(
        messages=[{"role": "user", "content": "hi"}],
        metadata={"tenant_id": "11111111-1111-4111-8111-111111111111", "run_id": "r-1"},
        model="ollama_chat/qwen3-coder:30b",
    )

    assert result["output_type"] == "text"
    assert result["output_ref"] == "hello world"
    assert result["finish_reason"] == "stop"
    assert result["actual_cost_cents"] == 0


@pytest.mark.asyncio
async def test_direct_adapter_passes_direct_true_to_bsvibe_llm():
    """``direct=True`` is the Decision #11 opt-out — without it
    ``bsvibe_llm`` routes through BSGateway and the per-tenant
    self-host endpoint never wins."""
    stub = _stub_client(_completion(""))
    adapter = DirectLLMAdapter(base_url=None, api_key="sk-x", client=stub)

    await adapter.execute(
        messages=[{"role": "user", "content": "hi"}],
        metadata={"tenant_id": "t1", "run_id": "r1"},
        model="gpt-4o",
    )

    stub.complete.assert_awaited_once()  # type: ignore[attr-defined]
    kwargs = stub.complete.await_args.kwargs  # type: ignore[attr-defined]
    assert kwargs["direct"] is True
    assert kwargs["model"] == "gpt-4o"


def _completion_with_raw(text: str, *, tool_calls: object = None) -> CompletionResult:
    """A ``CompletionResult`` carrying a litellm-shaped ``raw`` whose
    ``message.tool_calls`` is ``tool_calls`` (default: none)."""
    from types import SimpleNamespace

    message = SimpleNamespace(content=text, tool_calls=tool_calls)
    raw = SimpleNamespace(choices=[SimpleNamespace(message=message)])
    return CompletionResult(
        text=text,
        model="ollama_chat/qwen3-coder:30b",
        finish_reason="stop",
        prompt_tokens=0,
        completion_tokens=0,
        raw=raw,
    )


@pytest.mark.asyncio
async def test_direct_adapter_parses_qwen_text_format_tool_calls():
    """qwen3-coder via Ollama emits tool calls in its native
    ``<function=name><parameter=key>value</parameter></function>``
    text format, NOT structured ``message.tool_calls``. Ollama's
    ``/api/chat`` + litellm's ``ollama_chat`` provider do not reliably
    parse it back, so ``raw.tool_calls`` is empty and the call arrives
    as plain ``content``. The adapter must recover the call from the
    text or the dispatcher tool loop sees zero tool calls and fails
    every Request with ``no_workspace_write``."""
    qwen_text = (
        "<function=file_write>\n"
        "<parameter=path>\n"
        "greet.py\n"
        "</parameter>\n"
        "<parameter=content>\n"
        'def greet(name):\n    return f"Hello, {name}!"\n'
        "</parameter>\n"
        "</function>"
    )
    stub = _stub_client(_completion_with_raw(qwen_text))
    adapter = DirectLLMAdapter(base_url="http://localhost:11434", api_key="sk-x", client=stub)

    result = await adapter.execute(
        messages=[{"role": "user", "content": "hi"}],
        metadata={"tenant_id": "t1", "run_id": "r1"},
        model="ollama_chat/qwen3-coder:30b",
    )

    calls = result["tool_calls"]
    assert calls is not None and len(calls) == 1
    assert calls[0]["name"] == "file_write"
    assert calls[0]["arguments"]["path"] == "greet.py"
    assert calls[0]["arguments"]["content"] == 'def greet(name):\n    return f"Hello, {name}!"'


@pytest.mark.asyncio
async def test_direct_adapter_strips_qwen_markup_from_surfaced_text():
    """Once the ``<function=...>`` block is parsed into a tool call,
    the raw markup must not leak into ``output_ref`` — the dispatcher
    echoes that back into conversation history and would surface it as
    a deliverable summary. Genuine prose written alongside is kept."""
    qwen_text = (
        "I'll create the file now.\n"
        "<function=file_write>\n"
        "<parameter=path>\ngreet.py\n</parameter>\n"
        "<parameter=content>\ndef greet(name):\n    return name\n</parameter>\n"
        "</function>\n"
        "</tool_call>"
    )
    stub = _stub_client(_completion_with_raw(qwen_text))
    adapter = DirectLLMAdapter(base_url="http://localhost:11434", api_key="sk-x", client=stub)

    result = await adapter.execute(
        messages=[{"role": "user", "content": "hi"}],
        metadata={"tenant_id": "t1", "run_id": "r1"},
        model="ollama_chat/qwen3-coder:30b",
    )

    assert "<function=" not in result["output_ref"]
    assert "</tool_call>" not in result["output_ref"]
    assert result["output_ref"] == "I'll create the file now."
    assert result["tool_calls"][0]["name"] == "file_write"


@pytest.mark.asyncio
async def test_direct_adapter_text_format_parse_skipped_when_structured_present():
    """When the provider DID return structured ``tool_calls`` (SaaS
    vendors, or a correctly-templated Ollama model), the structured
    path wins — the text fallback must not double-count."""
    from types import SimpleNamespace

    structured = [
        SimpleNamespace(
            id="call_1",
            function=SimpleNamespace(name="file_read", arguments='{"path": "README.md"}'),
        )
    ]
    stub = _stub_client(_completion_with_raw("", tool_calls=structured))
    adapter = DirectLLMAdapter(base_url=None, api_key="sk-x", client=stub)

    result = await adapter.execute(
        messages=[{"role": "user", "content": "hi"}],
        metadata={"tenant_id": "t1", "run_id": "r1"},
        model="gpt-4o",
    )

    calls = result["tool_calls"]
    assert calls is not None and len(calls) == 1
    assert calls[0]["name"] == "file_read"
    assert calls[0]["arguments"] == {"path": "README.md"}


@pytest.mark.asyncio
async def test_direct_adapter_plain_prose_yields_no_tool_calls():
    """A genuine prose answer with no ``<function=...>`` block must
    still return ``tool_calls=None`` — the text parser must not
    false-positive on ordinary text."""
    stub = _stub_client(_completion_with_raw("I would create greet.py with a greet function."))
    adapter = DirectLLMAdapter(base_url="http://localhost:11434", api_key="sk-x", client=stub)

    result = await adapter.execute(
        messages=[{"role": "user", "content": "hi"}],
        metadata={"tenant_id": "t1", "run_id": "r1"},
        model="ollama_chat/qwen3-coder:30b",
    )

    assert result["tool_calls"] is None


@pytest.mark.asyncio
async def test_direct_adapter_forwards_self_host_endpoint_to_litellm():
    """A per-tenant ``ExecutorConfig`` self-host runtime (Ollama, vLLM)
    carries an explicit ``base_url``. ``bsvibe_llm`` drops ``api_base``
    for ``direct=True`` calls (``_resolve_api_base`` returns "") and
    ``LlmSettings`` silently ignores unknown kwargs, so the *only* wire
    that reaches ``litellm.acompletion`` is ``complete(extra=...)``.
    Without this the call falls back to litellm's ``localhost:11434``
    default and 500s on any host where Ollama isn't co-located."""
    stub = _stub_client(_completion("ok"))
    adapter = DirectLLMAdapter(
        base_url="http://host.docker.internal:11434",
        api_key="sk-tenant",
        client=stub,
    )

    await adapter.execute(
        messages=[{"role": "user", "content": "hi"}],
        metadata={"tenant_id": "t1", "run_id": "r1"},
        model="ollama_chat/qwen3-coder:30b",
    )

    kwargs = stub.complete.await_args.kwargs  # type: ignore[attr-defined]
    assert kwargs["extra"]["api_base"] == "http://host.docker.internal:11434"
    assert kwargs["extra"]["api_key"] == "sk-tenant"


@pytest.mark.asyncio
async def test_direct_adapter_omits_api_base_when_no_self_host_endpoint():
    """SaaS providers (``base_url=None``) infer the endpoint from the
    model id — the adapter must not pin a bogus ``api_base``. The
    ``api_key`` still flows so litellm authenticates the vendor call."""
    stub = _stub_client(_completion("ok"))
    adapter = DirectLLMAdapter(base_url=None, api_key="sk-vendor", client=stub)

    await adapter.execute(
        messages=[{"role": "user", "content": "hi"}],
        metadata={"tenant_id": "t1", "run_id": "r1"},
        model="gpt-4o",
    )

    kwargs = stub.complete.await_args.kwargs  # type: ignore[attr-defined]
    extra = kwargs.get("extra") or {}
    assert "api_base" not in extra
    assert extra["api_key"] == "sk-vendor"


@pytest.mark.asyncio
async def test_direct_adapter_coerces_metadata_to_run_audit_metadata():
    """``LlmClient.complete()`` requires a typed ``RunAuditMetadata``.
    The adapter accepts a free-form dict from the orchestrator and
    coerces it; required keys (`tenant_id`, `run_id`) are checked."""
    from bsvibe_llm import RunAuditMetadata

    stub = _stub_client(_completion(""))
    adapter = DirectLLMAdapter(base_url=None, api_key="sk-x", client=stub)

    await adapter.execute(
        messages=[{"role": "user", "content": "hi"}],
        metadata={
            "tenant_id": "11111111-1111-4111-8111-111111111111",
            "run_id": "r-1",
            "project_id": "p-1",
            "novel_key": "passthrough-via-extras",
        },
        model="gpt-4o",
    )

    kwargs = stub.complete.await_args.kwargs  # type: ignore[attr-defined]
    md = kwargs["metadata"]
    assert isinstance(md, RunAuditMetadata)
    assert md.tenant_id == "11111111-1111-4111-8111-111111111111"
    assert md.run_id == "r-1"
    assert md.project_id == "p-1"
    assert md.extras == {"novel_key": "passthrough-via-extras"}


@pytest.mark.asyncio
async def test_direct_adapter_rejects_metadata_without_required_keys():
    """Missing ``tenant_id`` / ``run_id`` is a contract failure, not a
    silent-best-effort. ``bsvibe_llm`` rejects anonymous traffic; we
    fail-fast at the adapter boundary so the orchestrator gets a
    structured error."""
    stub = _stub_client(_completion(""))
    adapter = DirectLLMAdapter(base_url=None, api_key="sk-x", client=stub)

    with pytest.raises(DirectLLMError):
        await adapter.execute(
            messages=[{"role": "user", "content": "hi"}],
            metadata={},
            model="gpt-4o",
        )
    stub.complete.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_direct_adapter_invokes_on_chunk_with_full_output():
    """``LlmClient.complete()`` is non-streaming. The adapter still
    fires ``on_chunk(full_text)`` once at the end so the orchestrator's
    SSE fan-out path stays unified across both executor kinds
    (BSGateway streams; direct sends one chunk)."""
    stub = _stub_client(_completion("the whole reply"))
    adapter = DirectLLMAdapter(base_url=None, api_key="sk-x", client=stub)

    chunks: list[str] = []

    async def on_chunk(delta: str) -> None:
        chunks.append(delta)

    await adapter.execute(
        messages=[{"role": "user", "content": "hi"}],
        metadata={"tenant_id": "t1", "run_id": "r1"},
        model="gpt-4o",
        on_chunk=on_chunk,
    )

    assert chunks == ["the whole reply"]


@pytest.mark.asyncio
async def test_direct_adapter_raises_direct_llm_error_on_provider_failure():
    """A failing ``LlmClient.complete()`` (network, provider 500, retry
    exhaustion) surfaces as ``DirectLLMError`` so the orchestrator can
    transition the RunAttempt to ``blocked`` uniformly."""
    stub = _stub_client(RuntimeError("upstream 503"))
    adapter = DirectLLMAdapter(base_url=None, api_key="sk-x", client=stub)

    with pytest.raises(DirectLLMError):
        await adapter.execute(
            messages=[{"role": "user", "content": "hi"}],
            metadata={"tenant_id": "t1", "run_id": "r1"},
            model="gpt-4o",
        )


def test_direct_adapter_conforms_to_executor_client_protocol():
    """Structural Protocol check — ``DirectLLMAdapter`` exposes the
    same ``execute()`` surface as ``BSGatewayClient``, so the resolver
    can hand back either without callers branching on the concrete
    class."""
    stub = _stub_client(_completion(""))
    adapter = DirectLLMAdapter(base_url=None, api_key="sk-x", client=stub)
    assert isinstance(adapter, ExecutorClient)


def test_bsgateway_client_conforms_to_executor_client_protocol() -> None:
    """The other half of the same contract: ``BSGatewayClient`` must
    also satisfy the Protocol so the resolver's single return type is
    legitimate."""
    from backend.src.core.bsgateway.client import BSGatewayClient

    client = BSGatewayClient(base_url="https://gateway.bsvibe.dev", api_key="x")
    assert isinstance(client, ExecutorClient)
    _ = Any  # silence unused-import lint when this test file evolves
