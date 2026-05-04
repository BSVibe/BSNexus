"""Executor guard — every selected ``ExecutorConfig`` resolves to one
of two adapter kinds, distinguished by infra dependency:

- ``executor_type="bsgateway"`` → :class:`BSGatewayAdapter` (BSVibe
  infra path; goes through BSGateway worker pool)
- ``executor_type="llm_api"`` → :class:`DirectLLMAdapter` (BSVibe-
  optional path; direct LLM call from BSNexus via litellm + MCP tool
  loop)

The legacy taxonomy (``claude_code`` / ``codex`` / ``opencode`` /
``worker``) is gone — alembic migration ``2026_05_04_collapse_executor_types``
lifts those into ``executor_type=bsgateway`` with the original value
preserved in ``config.model``. Unknown values at runtime return None
with a WARN so they don't dispatch silently.
"""

from __future__ import annotations

import uuid

import pytest

from backend.src.api.conversation import _build_adapter
from backend.src.core.bsgateway import BSGatewayAdapter
from backend.src.models import ExecutorConfig


async def _make_cfg(
    db_session,
    tenant_id,
    *,
    executor_type: str,
    config: dict,
    is_selected: bool = True,
) -> ExecutorConfig:
    row = ExecutorConfig(
        tenant_id=tenant_id,
        name=f"{executor_type}-default",
        executor_type=executor_type,
        config=config,
        is_selected=is_selected,
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    return row


# ─── No config / unknown type ────────────────────────────────────────


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
async def test_unknown_executor_type_returns_none(db_session, mock_tenant_id, seeded_tenant):
    """Post-2026-05-04, only ``bsgateway`` / ``llm_api`` are valid.
    Anything else (legacy ``claude_code``, typo ``mystery``, ...) is
    refused with a WARN — alembic should have migrated legacy values
    on upgrade."""
    await _make_cfg(
        db_session,
        mock_tenant_id,
        executor_type="claude_code",  # legacy — should never reach runtime
        config={"bsgateway_url": "http://gw.test", "bsgateway_api_key": "k"},
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


# ─── bsgateway adapter ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bsgateway_missing_url_returns_none(db_session, mock_tenant_id, seeded_tenant):
    await _make_cfg(
        db_session,
        mock_tenant_id,
        executor_type="bsgateway",
        config={"bsgateway_api_key": "k"},  # url missing
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
async def test_bsgateway_uses_cfg_model(db_session, mock_tenant_id, seeded_tenant):
    """``cfg.model`` is the actual model string BSGateway will route —
    can be a CLI alias (``claude_code``) or a litellm-style id
    (``anthropic/claude-3-5-sonnet``)."""
    await _make_cfg(
        db_session,
        mock_tenant_id,
        executor_type="bsgateway",
        config={
            "bsgateway_url": "http://gw.test",
            "bsgateway_api_key": "k",
            "model": "anthropic/claude-3-5-sonnet",
        },
    )
    adapter = await _build_adapter(
        db_session,
        mock_tenant_id,
        run_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
    )
    assert isinstance(adapter, BSGatewayAdapter)
    assert adapter._model == "anthropic/claude-3-5-sonnet"


@pytest.mark.asyncio
async def test_bsgateway_without_cfg_model_defaults_to_claude_code(
    db_session,
    mock_tenant_id,
    seeded_tenant,
):
    await _make_cfg(
        db_session,
        mock_tenant_id,
        executor_type="bsgateway",
        config={"bsgateway_url": "http://gw.test", "bsgateway_api_key": "k"},
    )
    adapter = await _build_adapter(
        db_session,
        mock_tenant_id,
        run_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
    )
    assert isinstance(adapter, BSGatewayAdapter)
    assert adapter._model == "claude_code"


@pytest.mark.asyncio
async def test_bsgateway_legacy_base_url_fallback(
    db_session,
    mock_tenant_id,
    seeded_tenant,
):
    """Pre-cutover rows used ``base_url`` directly. Dispatcher accepts
    it as the gateway URL during the migration window."""
    await _make_cfg(
        db_session,
        mock_tenant_id,
        executor_type="bsgateway",
        config={
            "base_url": "http://legacy-gateway.test",
            "api_key": "legacy-key",
            "model": "ollama/qwen3-coder:30b",
        },
    )
    adapter = await _build_adapter(
        db_session,
        mock_tenant_id,
        run_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
    )
    assert isinstance(adapter, BSGatewayAdapter)
    assert adapter._model == "ollama/qwen3-coder:30b"


@pytest.mark.asyncio
async def test_bsgateway_workspace_dir_resolved_from_project_id(
    db_session,
    mock_tenant_id,
    seeded_tenant,
):
    await _make_cfg(
        db_session,
        mock_tenant_id,
        executor_type="bsgateway",
        config={"bsgateway_url": "http://gw.test", "bsgateway_api_key": "k"},
    )
    project_id = uuid.uuid4()
    adapter = await _build_adapter(
        db_session,
        mock_tenant_id,
        run_id=uuid.uuid4(),
        project_id=project_id,
    )
    assert isinstance(adapter, BSGatewayAdapter)
    assert adapter._workspace_dir is not None
    assert str(project_id) in adapter._workspace_dir


# ─── llm_api → DirectLLMAdapter (Phase 2b) ──────────────────────


@pytest.mark.asyncio
async def test_llm_api_returns_direct_llm_adapter(
    db_session,
    mock_tenant_id,
    seeded_tenant,
):
    """``llm_api`` resolves to :class:`DirectLLMAdapter` — direct
    LLM call from BSNexus, BSVibe-optional path."""
    from backend.src.core.llm import DirectLLMAdapter

    cfg = await _make_cfg(
        db_session,
        mock_tenant_id,
        executor_type="llm_api",
        config={"model": "anthropic/claude-3-5-sonnet"},
    )
    cfg.api_key_encrypted = None
    cfg.config = {"model": "anthropic/claude-3-5-sonnet", "api_key": "sk-test"}
    await db_session.commit()

    adapter = await _build_adapter(
        db_session,
        mock_tenant_id,
        run_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
    )
    assert isinstance(adapter, DirectLLMAdapter)
    assert adapter._model == "anthropic/claude-3-5-sonnet"


@pytest.mark.asyncio
async def test_llm_api_missing_model_returns_none(
    db_session,
    mock_tenant_id,
    seeded_tenant,
):
    """``llm_api`` requires ``cfg.model`` — without it the dispatcher
    refuses rather than dispatching to a default model."""
    cfg = await _make_cfg(
        db_session,
        mock_tenant_id,
        executor_type="llm_api",
        config={"api_key": "sk-test"},
    )
    cfg.config = {"api_key": "sk-test"}
    await db_session.commit()

    adapter = await _build_adapter(
        db_session,
        mock_tenant_id,
        run_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
    )
    assert adapter is None


@pytest.mark.asyncio
async def test_llm_api_missing_api_key_returns_none(
    db_session,
    mock_tenant_id,
    seeded_tenant,
):
    """``llm_api`` calls a third-party LLM provider; an empty / unset
    key would 401 at the boundary. Refuse upfront with a WARN."""
    await _make_cfg(
        db_session,
        mock_tenant_id,
        executor_type="llm_api",
        config={"model": "openai/gpt-4o"},  # no api_key
    )
    adapter = await _build_adapter(
        db_session,
        mock_tenant_id,
        run_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
    )
    assert adapter is None
