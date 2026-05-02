"""Tests for BaseServiceClient (S2-1-X).

Decision #15: BaseServiceClient is a Protocol (structural typing, not ABC).
The shared HTTP/auth/timeout pattern between BSage knowledge_client and
BSupervisor audit_sink is consolidated into a single helper. The
``auth_provider`` callable is injected so Phase 0 P0.7 service JWT
issuance can replace the static api_key without changing adapter code.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


@pytest.mark.asyncio
async def test_base_service_client_sends_user_agent_and_bearer():
    """BaseServiceClient.request() should set BSNexus UA + Bearer header
    derived from ``auth_provider``.
    """
    from backend.src.core.clients.base import BaseServiceClient

    auth_provider: Callable[[], Awaitable[str]] = AsyncMock(return_value="tok-123")
    client = BaseServiceClient(
        base_url="https://example.test",
        auth_provider=auth_provider,
        user_agent="BSNexus/0.2 (+https://nexus.bsvibe.dev)",
        timeout_s=3.0,
    )

    captured: dict[str, Any] = {}

    async def fake_request(self, method, url, **kwargs):  # noqa: ANN001
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = kwargs.get("headers", {})
        return httpx.Response(200, json={"ok": True})

    with patch.object(httpx.AsyncClient, "request", new=fake_request):
        resp = await client.request("GET", "/api/health")

    assert resp.status_code == 200
    assert captured["url"] == "https://example.test/api/health"
    assert captured["headers"]["User-Agent"] == "BSNexus/0.2 (+https://nexus.bsvibe.dev)"
    assert captured["headers"]["Authorization"] == "Bearer tok-123"
    auth_provider.assert_awaited()


@pytest.mark.asyncio
async def test_base_service_client_omits_authorization_when_token_empty():
    """When auth_provider returns an empty string, no Authorization header
    is sent (so downstream gets a clean anonymous request rather than a
    malformed ``Bearer ``).
    """
    from backend.src.core.clients.base import BaseServiceClient

    client = BaseServiceClient(
        base_url="https://example.test",
        auth_provider=AsyncMock(return_value=""),
        user_agent="BSNexus/0.2",
    )

    captured: dict[str, Any] = {}

    async def fake_request(self, method, url, **kwargs):  # noqa: ANN001
        captured["headers"] = kwargs.get("headers", {})
        return httpx.Response(200, json={})

    with patch.object(httpx.AsyncClient, "request", new=fake_request):
        await client.request("GET", "/")

    assert "Authorization" not in captured["headers"]


@pytest.mark.asyncio
async def test_base_service_client_supports_sync_auth_provider():
    """auth_provider may be a sync callable. The client awaits its result
    only when the return value is awaitable.
    """
    from backend.src.core.clients.base import BaseServiceClient

    sync_provider = MagicMock(return_value="sync-token")
    client = BaseServiceClient(
        base_url="https://x.test",
        auth_provider=sync_provider,
        user_agent="BSNexus/0.2",
    )

    captured: dict[str, Any] = {}

    async def fake_request(self, method, url, **kwargs):  # noqa: ANN001
        captured["headers"] = kwargs.get("headers", {})
        return httpx.Response(200)

    with patch.object(httpx.AsyncClient, "request", new=fake_request):
        await client.request("GET", "/")

    assert captured["headers"]["Authorization"] == "Bearer sync-token"


@pytest.mark.asyncio
async def test_auth_provider_swappable_for_service_jwt():
    """Phase 0 P0.7 simulation — the same client instance can be swapped
    from a static api_key closure to a service-JWT minting closure with
    no code change inside the client. This locks in the interface.
    """
    from backend.src.core.clients.base import BaseServiceClient

    # Phase A current: static api_key closure
    static_provider = AsyncMock(return_value="static-api-key")
    client = BaseServiceClient(
        base_url="https://x.test",
        auth_provider=static_provider,
        user_agent="BSNexus/0.2",
    )

    captured = {"first": None, "second": None}

    async def fake_request_capture(self, method, url, **kwargs):  # noqa: ANN001
        captured.setdefault("calls", []).append(kwargs.get("headers", {}).get("Authorization"))
        return httpx.Response(200)

    with patch.object(httpx.AsyncClient, "request", new=fake_request_capture):
        await client.request("GET", "/")
        # Phase 0 P0.7: replace the closure with a service-JWT minter.
        client.set_auth_provider(AsyncMock(return_value="svc-jwt-eyJhbGc..."))
        await client.request("GET", "/")

    calls = captured["calls"]
    assert calls[0] == "Bearer static-api-key"
    assert calls[1] == "Bearer svc-jwt-eyJhbGc..."


@pytest.mark.asyncio
async def test_request_returns_none_on_timeout_and_logs_warning():
    """``safe_request`` swallows transient failures so callers can fall
    back to Noop without raising out of provider boundaries.
    """
    from backend.src.core.clients.base import BaseServiceClient

    client = BaseServiceClient(
        base_url="https://x.test",
        auth_provider=AsyncMock(return_value="tok"),
        user_agent="BSNexus/0.2",
        timeout_s=0.01,
    )

    async def boom(self, *args, **kwargs):  # noqa: ANN001
        raise httpx.TimeoutException("simulated")

    with patch.object(httpx.AsyncClient, "request", new=boom):
        result = await client.safe_request("GET", "/api/health", event="health_check")

    assert result is None


@pytest.mark.asyncio
async def test_request_returns_none_on_http_error_keeps_cancelled_error_propagating():
    """``safe_request`` MUST re-raise ``asyncio.CancelledError`` so cancel
    semantics are preserved (per S2-1 M9 cleanup rule).
    """
    import asyncio

    from backend.src.core.clients.base import BaseServiceClient

    client = BaseServiceClient(
        base_url="https://x.test",
        auth_provider=AsyncMock(return_value="tok"),
        user_agent="BSNexus/0.2",
    )

    async def cancel(self, *args, **kwargs):  # noqa: ANN001
        raise asyncio.CancelledError("test")

    with patch.object(httpx.AsyncClient, "request", new=cancel):
        with pytest.raises(asyncio.CancelledError):
            await client.safe_request("GET", "/api/health", event="health_check")


@pytest.mark.asyncio
async def test_protocol_runtime_check_accepts_base_service_client():
    """``BaseServiceClient`` is a structural Protocol; concrete subclasses
    of the same shape are accepted by ``isinstance`` runtime checks (per
    decision #15).
    """
    from backend.src.core.clients.base import (
        BaseServiceClient,
        ServiceClientProtocol,
    )

    client = BaseServiceClient(
        base_url="https://x.test",
        auth_provider=AsyncMock(return_value=""),
        user_agent="UA",
    )
    # Protocol with @runtime_checkable should accept the concrete impl.
    assert isinstance(client, ServiceClientProtocol)
