"""G6.8 — ExecutorConfig.extra_config['temperature'] passthrough.

The variance discovered in the 2026-05-11 G6.7 baseline (per-run
strict_pass [2, 5, 0] on the same prompt+workspace) needs a knob to
test whether the variance is sampling-driven or capability-intrinsic.
Setting temperature=0 forces near-deterministic decoding so the next
measurement cycle can attribute the residual variance to model
capability.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest
from bsvibe_llm import CompletionResult, RunAuditMetadata

from backend.src.core.executor_config.resolver import _extract_temperature, resolve_executor
from backend.src.core.llm.direct_client import DirectLLMAdapter
from backend.src.models.executor_config import ExecutorConfig, ExecutorKind


def _make_config(*, temperature: Any = None, tenant_id: uuid.UUID | None = None) -> ExecutorConfig:
    extra = {} if temperature is None else {"temperature": temperature}
    return ExecutorConfig(
        tenant_id=tenant_id or uuid.uuid4(),
        kind=ExecutorKind.llm_api,
        base_url="http://localhost:11434",
        model="ollama_chat/qwen3-coder:30b",
        api_key_encrypted=None,
        extra_config=extra,
    )


def test_extract_temperature_returns_none_when_unset():
    assert _extract_temperature(_make_config()) is None


def test_extract_temperature_parses_float_from_extra_config():
    assert _extract_temperature(_make_config(temperature=0.0)) == 0.0
    assert _extract_temperature(_make_config(temperature=0.7)) == 0.7
    assert _extract_temperature(_make_config(temperature="0.3")) == 0.3


def test_extract_temperature_rejects_garbage_and_returns_none():
    assert _extract_temperature(_make_config(temperature="not-a-float")) is None
    assert _extract_temperature(_make_config(temperature={"nested": "no"})) is None


@pytest.mark.asyncio
async def test_direct_llm_adapter_forwards_temperature_to_underlying_client():
    """When ExecutorConfig sets extra_config['temperature']=0.0, the
    adapter forwards it to ``bsvibe_llm.LlmClient.complete(temperature=0)``."""
    client = AsyncMock()
    client.complete = AsyncMock(
        return_value=CompletionResult(
            text="ok",
            model="m",
            finish_reason="stop",
            prompt_tokens=0,
            completion_tokens=0,
            raw=None,
        )
    )
    adapter = DirectLLMAdapter(
        base_url="http://localhost:11434",
        api_key="",
        temperature=0.0,
        client=client,
    )

    await adapter.execute(
        messages=[{"role": "user", "content": "hi"}],
        metadata={"tenant_id": "t", "run_id": "r"},
        model="ollama_chat/qwen3-coder:30b",
    )

    call_kwargs = client.complete.await_args.kwargs
    assert call_kwargs["temperature"] == 0.0
    assert isinstance(call_kwargs["metadata"], RunAuditMetadata)


@pytest.mark.asyncio
async def test_direct_llm_adapter_omits_temperature_when_unset():
    """No temperature in extra_config → adapter passes ``None`` and
    litellm falls back to provider default."""
    client = AsyncMock()
    client.complete = AsyncMock(
        return_value=CompletionResult(
            text="ok",
            model="m",
            finish_reason="stop",
            prompt_tokens=0,
            completion_tokens=0,
            raw=None,
        )
    )
    adapter = DirectLLMAdapter(
        base_url="http://localhost:11434",
        api_key="",
        client=client,
    )

    await adapter.execute(
        messages=[{"role": "user", "content": "hi"}],
        metadata={"tenant_id": "t", "run_id": "r"},
        model="ollama_chat/qwen3-coder:30b",
    )

    assert client.complete.await_args.kwargs["temperature"] is None


@pytest.mark.asyncio
async def test_resolver_propagates_temperature_via_extra_config(db_session, mock_tenant_id, seeded_tenant):
    """End-to-end through the resolver: configured temperature lands
    on the resolved DirectLLMAdapter instance."""
    row = ExecutorConfig(
        tenant_id=mock_tenant_id,
        kind=ExecutorKind.llm_api,
        base_url="http://localhost:11434",
        model="ollama_chat/qwen3-coder:30b",
        api_key_encrypted=None,
        extra_config={"temperature": 0.0},
    )
    db_session.add(row)
    await db_session.commit()

    adapter = await resolve_executor(tenant_id=mock_tenant_id, session=db_session)
    assert isinstance(adapter, DirectLLMAdapter)
    assert adapter._temperature == 0.0  # noqa: SLF001 — direct inspection is the point


@pytest.mark.asyncio
async def test_resolver_leaves_temperature_none_when_extra_config_omits_it(db_session, mock_tenant_id, seeded_tenant):
    row = ExecutorConfig(
        tenant_id=mock_tenant_id,
        kind=ExecutorKind.llm_api,
        base_url="http://localhost:11434",
        model="ollama_chat/qwen3-coder:30b",
        api_key_encrypted=None,
        extra_config={},
    )
    db_session.add(row)
    await db_session.commit()

    adapter = await resolve_executor(tenant_id=mock_tenant_id, session=db_session)
    assert isinstance(adapter, DirectLLMAdapter)
    assert adapter._temperature is None  # noqa: SLF001
