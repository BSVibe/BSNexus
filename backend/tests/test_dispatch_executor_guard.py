"""Executor guard — backend must never make an LLM call on behalf of a
tenant whose ``ExecutorConfig`` doesn't explicitly authorize one.

The layering:
  - LLM is the lowest layer (litellm.acompletion / claude CLI / …)
  - The **executor** is the abstraction directly above. It decides
    *where* the LLM runs: backend process (generic_llm / bsgateway) or
    a remote worker (worker).

A worker-only tenant still gets runs executed — on the worker — so the
backend never initiates a paid LLM call on their behalf.

`_build_adapter` is the single dispatcher that translates an
``ExecutorConfig`` into an orchestrator executor.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.src.api.conversation import _build_adapter
from backend.src.core.orchestrator_adapter import LiteLLMOrchestratorAdapter
from backend.src.core.worker_adapter import WorkerDispatchAdapter
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
    assert (
        await _build_adapter(
            db_session,
            mock_tenant_id,
            run_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
        )
        is None
    )


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
    adapter = await _build_adapter(
        db_session,
        mock_tenant_id,
        run_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
    )
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
    assert (
        await _build_adapter(
            db_session,
            mock_tenant_id,
            run_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
        )
        is None
    )


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
    adapter = await _build_adapter(
        db_session,
        mock_tenant_id,
        run_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
    )
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
    assert (
        await _build_adapter(
            db_session,
            mock_tenant_id,
            run_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
        )
        is None
    )


@pytest.mark.parametrize(
    "exec_type",
    ["claude_code", "codex"],
)
@pytest.mark.asyncio
async def test_local_cli_executor_types_return_none(
    db_session, mock_tenant_id, seeded_tenant, exec_type
):
    """``claude_code``/``codex`` would require a local CLI on the backend
    host; a multi-tenant backend doesn't invoke those. Returning None
    keeps the backend from surprise-billing the tenant via a direct LLM
    fallback.
    """
    await _make_cfg(
        db_session,
        mock_tenant_id,
        executor_type=exec_type,
        config={},
    )
    assert (
        await _build_adapter(
            db_session,
            mock_tenant_id,
            run_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
        )
        is None
    )


@pytest.mark.asyncio
async def test_worker_default_without_stream_manager_returns_none(
    db_session, mock_tenant_id, seeded_tenant
):
    """No stream_manager → we cannot publish to the worker's queue, so
    fall back to None (run waits). Never falls through to LiteLLM."""
    await _make_cfg(
        db_session,
        mock_tenant_id,
        executor_type="worker",
        config={},
    )
    assert (
        await _build_adapter(
            db_session,
            mock_tenant_id,
            run_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            stream_manager=None,
        )
        is None
    )


@pytest.mark.asyncio
async def test_worker_default_without_online_worker_returns_none(
    db_session, mock_tenant_id, seeded_tenant
):
    """Worker type but no worker has heartbeated recently → run waits."""
    await _make_cfg(
        db_session,
        mock_tenant_id,
        executor_type="worker",
        config={},
    )
    stream_manager = MagicMock()
    stream_manager.publish = AsyncMock()
    assert (
        await _build_adapter(
            db_session,
            mock_tenant_id,
            run_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            stream_manager=stream_manager,
        )
        is None
    )


@pytest.mark.asyncio
async def test_worker_default_with_online_worker_returns_worker_adapter(
    db_session, mock_tenant_id, seeded_tenant
):
    """Happy path: worker-only tenant + an online worker exists →
    dispatch runs to the worker (no backend-side LLM call).
    """
    import hashlib
    from datetime import datetime, timezone

    from backend.src.models import Worker

    await _make_cfg(
        db_session,
        mock_tenant_id,
        executor_type="worker",
        config={},
    )
    worker = Worker(
        tenant_id=mock_tenant_id,
        name="dev-host",
        labels=[],
        capabilities=["claude_code"],
        status="online",
        last_heartbeat=datetime.now(timezone.utc),
        token_hash=hashlib.sha256(b"fake").hexdigest(),
        is_active=True,
    )
    db_session.add(worker)
    await db_session.commit()
    await db_session.refresh(worker)

    stream_manager = MagicMock()
    stream_manager.publish = AsyncMock()

    adapter = await _build_adapter(
        db_session,
        mock_tenant_id,
        run_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        stream_manager=stream_manager,
    )
    assert isinstance(adapter, WorkerDispatchAdapter)
    assert adapter._worker_id == worker.id


@pytest.mark.asyncio
async def test_only_default_is_consulted(db_session, mock_tenant_id, seeded_tenant):
    """A second, non-default generic_llm row must not influence dispatch
    — only ``is_default=True`` decides which executor handles runs.

    Guarantees: a worker-default tenant with a non-default generic_llm
    config does NOT get its runs silently routed through LiteLLM.
    """
    # Default: worker — no online worker, no stream manager → waits.
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

    assert (
        await _build_adapter(
            db_session,
            mock_tenant_id,
            run_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            stream_manager=None,
        )
        is None
    )


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

    assert (
        await _build_adapter(
            db_session,
            mock_tenant_id,
            run_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
        )
        is None
    )
