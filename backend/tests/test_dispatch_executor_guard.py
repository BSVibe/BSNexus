"""Executor guard — backend must never make an LLM call on behalf of a
tenant whose ``ExecutorConfig`` doesn't explicitly authorize one.

The `_build_adapter` dispatcher returns a live ``LiteLLMOrchestratorAdapter``
only for ``generic_llm`` + ``bsgateway`` configs. For ``worker``,
``claude_code``, ``codex`` (or no config at all), it returns ``None`` —
the run pauses at ``running`` state without touching an LLM API.

Without this guard, a user who signed up only to register a remote
worker could get a surprise invoice because the backend fell through
to a direct LiteLLM call.
"""

from __future__ import annotations

import uuid

import pytest

from backend.src.api.conversation import _build_adapter
from backend.src.core.orchestrator_adapter import LiteLLMOrchestratorAdapter
from backend.src.models import ExecutorConfig


async def _make_cfg(
    db_session,
    tenant_id,
    *,
    executor_type: str,
    config: dict,
    is_default: bool = True,
) -> ExecutorConfig:
    row = ExecutorConfig(
        tenant_id=tenant_id,
        name=f"{executor_type}-default",
        executor_type=executor_type,
        config=config,
        is_default=is_default,
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    return row


@pytest.mark.asyncio
async def test_no_default_returns_none(db_session, mock_tenant_id, seeded_tenant):
    assert await _build_adapter(db_session, mock_tenant_id) is None


@pytest.mark.asyncio
async def test_generic_llm_default_returns_adapter(
    db_session, mock_tenant_id, seeded_tenant
):
    await _make_cfg(
        db_session,
        mock_tenant_id,
        executor_type="generic_llm",
        config={
            "model": "ollama/glm-4.7-flash:latest",
            "api_key": "unused",
            "base_url": "http://localhost:11434",
        },
    )
    adapter = await _build_adapter(db_session, mock_tenant_id)
    assert isinstance(adapter, LiteLLMOrchestratorAdapter)


@pytest.mark.asyncio
async def test_generic_llm_without_model_returns_none(
    db_session, mock_tenant_id, seeded_tenant
):
    await _make_cfg(
        db_session,
        mock_tenant_id,
        executor_type="generic_llm",
        config={},  # empty — no model field
    )
    assert await _build_adapter(db_session, mock_tenant_id) is None


@pytest.mark.asyncio
async def test_bsgateway_default_returns_adapter_pointed_at_gateway(
    db_session, mock_tenant_id, seeded_tenant
):
    await _make_cfg(
        db_session,
        mock_tenant_id,
        executor_type="bsgateway",
        config={
            "bsgateway_url": "https://gateway.bsvibe.dev",
            "bsgateway_api_key": "bsg-secret",
        },
    )
    adapter = await _build_adapter(db_session, mock_tenant_id)
    assert isinstance(adapter, LiteLLMOrchestratorAdapter)
    assert adapter._base_url == "https://gateway.bsvibe.dev"
    assert adapter._api_key == "bsg-secret"


@pytest.mark.asyncio
async def test_bsgateway_without_url_returns_none(
    db_session, mock_tenant_id, seeded_tenant
):
    await _make_cfg(
        db_session,
        mock_tenant_id,
        executor_type="bsgateway",
        config={},
    )
    assert await _build_adapter(db_session, mock_tenant_id) is None


@pytest.mark.parametrize(
    "exec_type",
    ["worker", "claude_code", "codex"],
)
@pytest.mark.asyncio
async def test_non_llm_executor_types_return_none(
    db_session, mock_tenant_id, seeded_tenant, exec_type
):
    """Users whose default executor is a worker/CLI must never cause the
    backend to fall through to a direct LiteLLM call."""
    await _make_cfg(
        db_session,
        mock_tenant_id,
        executor_type=exec_type,
        config={},
    )
    assert await _build_adapter(db_session, mock_tenant_id) is None


@pytest.mark.asyncio
async def test_only_default_is_consulted(db_session, mock_tenant_id, seeded_tenant):
    """A second, non-default generic_llm row must not influence dispatch
    — only ``is_default=True`` decides."""
    # Default: worker (no LLM allowed)
    await _make_cfg(
        db_session,
        mock_tenant_id,
        executor_type="worker",
        config={},
        is_default=True,
    )
    # Extra generic_llm config, but NOT default
    extra = ExecutorConfig(
        tenant_id=mock_tenant_id,
        name="extra",
        executor_type="generic_llm",
        config={"model": "openai/gpt-4o", "api_key": "sk-x"},
        is_default=False,
    )
    db_session.add(extra)
    await db_session.commit()

    # Must still return None — the worker default takes precedence and
    # the generic_llm config is not to be auto-promoted.
    assert await _build_adapter(db_session, mock_tenant_id) is None


@pytest.mark.asyncio
async def test_foreign_tenant_default_ignored(
    db_session, mock_tenant_id, seeded_tenant
):
    """Another tenant's default must never leak through."""
    from backend.src.models import Tenant

    other_tid = uuid.uuid4()
    other = Tenant(
        id=other_tid,
        name="Other",
        slug=f"o-{uuid.uuid4().hex[:8]}",
        owner_user_id="x",
    )
    db_session.add(other)
    await db_session.commit()

    await _make_cfg(
        db_session,
        other_tid,
        executor_type="generic_llm",
        config={"model": "openai/gpt-4o", "api_key": "sk-x"},
    )

    assert await _build_adapter(db_session, mock_tenant_id) is None
