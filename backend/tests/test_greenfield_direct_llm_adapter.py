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
