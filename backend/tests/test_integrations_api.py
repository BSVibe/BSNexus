"""Integrations API — per-tenant provider config CRUD + test-connection."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


@pytest.mark.asyncio
async def test_list_returns_defaults_when_unset(client):
    resp = await client.get("/api/v1/integrations", headers={"Authorization": "Bearer fake"})
    assert resp.status_code == 200
    data = resp.json()
    assert set(data.keys()) == {"bsage", "bsupervisor"}
    for cfg in data.values():
        assert cfg["enabled"] is False
        assert cfg["base_url"] is None
        assert cfg["has_api_key"] is False


@pytest.mark.asyncio
async def test_update_persists_enabled_and_base_url(client):
    resp = await client.patch(
        "/api/v1/integrations/bsage",
        json={"enabled": True, "base_url": "http://sage.example.com"},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True
    assert resp.json()["base_url"] == "http://sage.example.com"

    listed = (await client.get("/api/v1/integrations", headers={"Authorization": "Bearer fake"})).json()
    assert listed["bsage"]["enabled"] is True


@pytest.mark.asyncio
async def test_update_encrypts_api_key_and_never_returns_it(client):
    resp = await client.patch(
        "/api/v1/integrations/bsupervisor",
        json={"enabled": True, "base_url": "http://gw", "api_key": "secret-123"},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_api_key"] is True
    assert "api_key" not in body


@pytest.mark.asyncio
async def test_update_rejects_unknown_provider(client):
    resp = await client.patch(
        "/api/v1/integrations/madeup",
        json={"enabled": True},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_test_connection_disabled_returns_disabled(client):
    resp = await client.post(
        "/api/v1/integrations/bsage/test",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "disabled"
    assert resp.json()["ok"] is False


@pytest.mark.asyncio
async def test_test_connection_ok_when_http_200(client):
    # configure first
    await client.patch(
        "/api/v1/integrations/bsage",
        json={"enabled": True, "base_url": "http://sage"},
        headers={"Authorization": "Bearer fake"},
    )

    mock_response = MagicMock()
    mock_response.status_code = 200
    # Post-S2-1-X the probe runs through BaseServiceClient which calls
    # AsyncClient.request(method, url, ...) instead of get(...).
    async_client = AsyncMock()
    async_client.request = AsyncMock(return_value=mock_response)
    async_client.get = AsyncMock(return_value=mock_response)
    async_client.__aenter__ = AsyncMock(return_value=async_client)
    async_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=async_client):
        resp = await client.post(
            "/api/v1/integrations/bsage/test",
            headers={"Authorization": "Bearer fake"},
        )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["status"] == "healthy"


@pytest.mark.asyncio
async def test_test_connection_unreachable_on_timeout(client):
    await client.patch(
        "/api/v1/integrations/bsupervisor",
        json={"enabled": True, "base_url": "http://gw"},
        headers={"Authorization": "Bearer fake"},
    )

    async_client = AsyncMock()
    async_client.request = AsyncMock(side_effect=httpx.TimeoutException("boom"))
    async_client.get = AsyncMock(side_effect=httpx.TimeoutException("boom"))
    async_client.__aenter__ = AsyncMock(return_value=async_client)
    async_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=async_client):
        resp = await client.post(
            "/api/v1/integrations/bsupervisor/test",
            headers={"Authorization": "Bearer fake"},
        )
    assert resp.json()["ok"] is False
    assert resp.json()["status"] == "unreachable"
