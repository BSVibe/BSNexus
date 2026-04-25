"""Tests for BSupervisorAuditSink fail-open/closed + NoopAuditSink."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from backend.src.core.audit import (
    BSupervisorAuditSink,
    NoopAuditSink,
    resolve_audit_sink,
)
from backend.src.core.integrations.config import AuditProviderConfig


def _fake_run() -> SimpleNamespace:
    return SimpleNamespace(
        id="00000000-0000-0000-0000-000000000011",
        tenant_id="00000000-0000-0000-0000-000000000012",
        project_id="00000000-0000-0000-0000-000000000013",
        status="running",
        actual_cost_cents=0,
    )


def _fake_snapshot() -> SimpleNamespace:
    return SimpleNamespace(
        id="00000000-0000-0000-0000-000000000021",
        tools_allowed=["read", "write"],
        persona_label="builder",
    )


@pytest.mark.asyncio
async def test_noop_sink_always_allows():
    sink = NoopAuditSink()
    result = await sink.preflight(_fake_run(), _fake_snapshot())
    assert result.blocked is False
    assert result.degraded is False


@pytest.mark.asyncio
async def test_noop_sink_post_is_safe():
    sink = NoopAuditSink()
    await sink.emit_post(_fake_run(), {"status": "done"})


def test_resolve_audit_sink_returns_noop_when_disabled():
    assert isinstance(resolve_audit_sink(None), NoopAuditSink)

    disabled = AuditProviderConfig(enabled=False, base_url="http://s", api_key="k")
    assert isinstance(resolve_audit_sink(disabled), NoopAuditSink)


def test_resolve_audit_sink_returns_bsupervisor_when_configured():
    cfg = AuditProviderConfig(enabled=True, base_url="http://supervisor", api_key="k")
    sink = resolve_audit_sink(cfg)
    assert isinstance(sink, BSupervisorAuditSink)


@pytest.mark.asyncio
async def test_bsupervisor_preflight_allowed_when_ok():
    sink = BSupervisorAuditSink("http://supervisor", "k")

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"allowed": True})

    async_client = AsyncMock()
    async_client.post = AsyncMock(return_value=mock_response)
    async_client.__aenter__ = AsyncMock(return_value=async_client)
    async_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=async_client):
        result = await sink.preflight(_fake_run(), _fake_snapshot())

    assert result.blocked is False
    assert result.degraded is False


@pytest.mark.asyncio
async def test_bsupervisor_preflight_blocked_when_denied():
    sink = BSupervisorAuditSink("http://supervisor", None)

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(
        return_value={"allowed": False, "reason": "rule X violated"}
    )

    async_client = AsyncMock()
    async_client.post = AsyncMock(return_value=mock_response)
    async_client.__aenter__ = AsyncMock(return_value=async_client)
    async_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=async_client):
        result = await sink.preflight(_fake_run(), _fake_snapshot())

    assert result.blocked is True
    assert result.reason == "rule X violated"
    assert result.degraded is False


@pytest.mark.asyncio
async def test_bsupervisor_preflight_fails_open_on_timeout():
    sink = BSupervisorAuditSink("http://supervisor", None, fail_mode="open")

    async_client = AsyncMock()
    async_client.post = AsyncMock(side_effect=httpx.TimeoutException("boom"))
    async_client.__aenter__ = AsyncMock(return_value=async_client)
    async_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=async_client):
        result = await sink.preflight(_fake_run(), _fake_snapshot())

    assert result.blocked is False
    assert result.degraded is True


@pytest.mark.asyncio
async def test_bsupervisor_preflight_fails_closed_when_configured():
    sink = BSupervisorAuditSink("http://supervisor", None, fail_mode="closed")

    async_client = AsyncMock()
    async_client.post = AsyncMock(side_effect=httpx.TimeoutException("boom"))
    async_client.__aenter__ = AsyncMock(return_value=async_client)
    async_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=async_client):
        result = await sink.preflight(_fake_run(), _fake_snapshot())

    assert result.blocked is True
    assert result.degraded is True


@pytest.mark.asyncio
async def test_audit_provider_config_defaults():
    cfg = AuditProviderConfig(enabled=True, base_url="http://s", api_key=None)
    assert cfg.timeout_ms == 200
    assert cfg.fail_mode == "open"


@pytest.mark.asyncio
async def test_audit_provider_config_reads_extras():
    cfg = AuditProviderConfig(
        enabled=True,
        base_url="http://s",
        api_key=None,
        extra={"timeout_ms": 500, "fail_mode": "closed"},
    )
    assert cfg.timeout_ms == 500
    assert cfg.fail_mode == "closed"
