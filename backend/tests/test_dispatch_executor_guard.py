"""Executor guard — every ``ExecutorConfig`` builds a single adapter type.

After the Direction reset 2026-05-03, all dispatch routes through
:class:`BSGatewayAdapter` (BSNexus core no longer makes outbound LLM
calls; BSGateway holds that role). This test pins:

- The adapter type returned for each ``executor_type`` is always
  ``BSGatewayAdapter``.
- The ``model`` string sent to BSGateway is derived from the executor
  type (claude_code / codex / opencode literal) or from
  ``cfg.model`` (bsgateway / generic_llm).
- Missing ``bsgateway_url`` (with no ``base_url`` legacy fallback)
  returns None — the dispatcher refuses to invent a gateway endpoint.
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
    await _make_cfg(
        db_session,
        mock_tenant_id,
        executor_type="mystery",
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


@pytest.mark.asyncio
async def test_missing_gateway_url_returns_none(db_session, mock_tenant_id, seeded_tenant):
    await _make_cfg(
        db_session,
        mock_tenant_id,
        executor_type="claude_code",
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


# ─── First-class CLI executors map to literal model strings ─────────


@pytest.mark.asyncio
@pytest.mark.parametrize("exec_type", ["claude_code", "codex", "opencode"])
async def test_cli_executor_type_used_as_model_literal(
    exec_type: str,
    db_session,
    mock_tenant_id,
    seeded_tenant,
):
    await _make_cfg(
        db_session,
        mock_tenant_id,
        executor_type=exec_type,
        config={"bsgateway_url": "http://gw.test", "bsgateway_api_key": "k"},
    )
    adapter = await _build_adapter(
        db_session,
        mock_tenant_id,
        run_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
    )
    assert isinstance(adapter, BSGatewayAdapter)
    assert adapter._model == exec_type


# ─── bsgateway / generic_llm pull model from cfg.model ──────────────


@pytest.mark.asyncio
async def test_bsgateway_uses_cfg_model(db_session, mock_tenant_id, seeded_tenant):
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
async def test_generic_llm_uses_cfg_model(db_session, mock_tenant_id, seeded_tenant):
    await _make_cfg(
        db_session,
        mock_tenant_id,
        executor_type="generic_llm",
        config={
            "bsgateway_url": "http://gw.test",
            "bsgateway_api_key": "k",
            "model": "openai/gpt-4o-mini",
        },
    )
    adapter = await _build_adapter(
        db_session,
        mock_tenant_id,
        run_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
    )
    assert isinstance(adapter, BSGatewayAdapter)
    assert adapter._model == "openai/gpt-4o-mini"


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


# ─── Legacy base_url fallback ────────────────────────────────────────


@pytest.mark.asyncio
async def test_generic_llm_base_url_fallback_routes_through_bsgateway(
    db_session,
    mock_tenant_id,
    seeded_tenant,
):
    """Pre-cutover ExecutorConfig rows used ``base_url`` directly. The
    dispatcher accepts it as the gateway URL during the migration window.
    """
    await _make_cfg(
        db_session,
        mock_tenant_id,
        executor_type="generic_llm",
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


# ─── Workspace dir comes from project_workspace_path ────────────────


@pytest.mark.asyncio
async def test_workspace_dir_resolved_from_project_id(
    db_session,
    mock_tenant_id,
    seeded_tenant,
):
    await _make_cfg(
        db_session,
        mock_tenant_id,
        executor_type="claude_code",
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
    # workspace_dir is an absolute filesystem path containing the project id.
    assert adapter._workspace_dir is not None
    assert str(project_id) in adapter._workspace_dir
