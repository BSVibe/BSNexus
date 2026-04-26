"""Tests for ServiceJWTMinter (P0.7 — service JWT auth_provider).

Decision #16 (Lockin): service JWT = audience-scoped (`aud: bsage`) +
scope claim (`scope: bsage.read`). The minter calls BSVibe-Auth's
``POST /api/service-tokens/issue`` endpoint and caches the returned
JWT per ``(audience, tenant_id, scope)`` tuple with a TTL-1m margin.

This minter produces an ``auth_provider`` closure compatible with
``BaseServiceClient.auth_provider`` — feeding it into the existing
adapters swaps the static api_key auth path for a service-JWT path
without touching adapter code (Decision #15 contract).
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import httpx
import pytest


@pytest.mark.asyncio
async def test_minter_returns_token_from_bsvibe_auth():
    """Minter posts to ``/api/service-tokens/issue`` and returns the
    ``access_token`` field."""
    from backend.src.core.service_auth import ServiceJWTMinter

    minter = ServiceJWTMinter(
        bsvibe_auth_url="https://auth.bsvibe.dev",
        bootstrap_token_provider=lambda: "bootstrap-admin-token",
    )

    fake_resp = MagicMock(spec=httpx.Response)
    fake_resp.status_code = 200
    fake_resp.raise_for_status = MagicMock()
    fake_resp.json = MagicMock(
        return_value={
            "access_token": "svc-jwt-eyJhbGc...",
            "expires_in": 600,
            "token_type": "service",
        }
    )

    captured: dict = {}

    async def fake_post(self, url, **kwargs):  # noqa: ANN001
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        captured["headers"] = kwargs.get("headers", {})
        return fake_resp

    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        token = await minter.mint(
            audience="bsage",
            tenant_id="11111111-1111-4111-8111-111111111111",
            scope=["bsage.read"],
        )

    assert token == "svc-jwt-eyJhbGc..."
    assert captured["url"] == "https://auth.bsvibe.dev/api/service-tokens/issue"
    body = captured["json"]
    assert body["audience"] == "bsage"
    assert body["scope"] == ["bsage.read"]
    assert body["tenant_id"] == "11111111-1111-4111-8111-111111111111"
    assert captured["headers"]["Authorization"] == "Bearer bootstrap-admin-token"


@pytest.mark.asyncio
async def test_minter_caches_token_within_ttl():
    """Two consecutive ``mint`` calls with the same ``(audience,
    tenant, scope)`` MUST hit the upstream only once — the second
    call returns the cached token with TTL-1m margin."""
    from backend.src.core.service_auth import ServiceJWTMinter

    minter = ServiceJWTMinter(
        bsvibe_auth_url="https://auth.bsvibe.dev",
        bootstrap_token_provider=lambda: "boot",
    )

    fake_resp = MagicMock(spec=httpx.Response)
    fake_resp.status_code = 200
    fake_resp.raise_for_status = MagicMock()
    fake_resp.json = MagicMock(
        return_value={"access_token": "svc-cached", "expires_in": 600},
    )

    call_count = {"n": 0}

    async def fake_post(self, url, **kwargs):  # noqa: ANN001
        call_count["n"] += 1
        return fake_resp

    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        token1 = await minter.mint(audience="bsage", tenant_id="t1", scope=["bsage.read"])
        token2 = await minter.mint(audience="bsage", tenant_id="t1", scope=["bsage.read"])

    assert token1 == token2 == "svc-cached"
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_minter_separate_cache_per_audience_and_scope():
    """Different (audience, scope) tuples have separate cache entries."""
    from backend.src.core.service_auth import ServiceJWTMinter

    minter = ServiceJWTMinter(
        bsvibe_auth_url="https://auth.bsvibe.dev",
        bootstrap_token_provider=lambda: "boot",
    )

    responses = [
        ("svc-bsage", ["bsage.read"]),
        ("svc-bsupervisor", ["bsupervisor.write"]),
    ]
    idx = {"i": 0}

    async def fake_post(self, url, **kwargs):  # noqa: ANN001
        token, _scope = responses[idx["i"]]
        idx["i"] += 1
        r = MagicMock(spec=httpx.Response)
        r.status_code = 200
        r.raise_for_status = MagicMock()
        r.json = MagicMock(return_value={"access_token": token, "expires_in": 600})
        return r

    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        t1 = await minter.mint(audience="bsage", tenant_id="t1", scope=["bsage.read"])
        t2 = await minter.mint(audience="bsupervisor", tenant_id="t1", scope=["bsupervisor.write"])

    assert t1 == "svc-bsage"
    assert t2 == "svc-bsupervisor"
    assert idx["i"] == 2  # both fetched, no cross-cache


@pytest.mark.asyncio
async def test_minter_refreshes_after_expiry():
    """When the cached token's TTL minus margin has elapsed, a new
    mint call MUST hit the upstream again. We use a near-zero
    expires_in to force expiry deterministically."""
    from backend.src.core.service_auth import ServiceJWTMinter

    minter = ServiceJWTMinter(
        bsvibe_auth_url="https://auth.bsvibe.dev",
        bootstrap_token_provider=lambda: "boot",
        # 60s safety margin > 1s expires_in → instant expiry
        safety_margin_s=60,
    )

    tokens = ["svc-1", "svc-2"]
    idx = {"i": 0}

    async def fake_post(self, url, **kwargs):  # noqa: ANN001
        r = MagicMock(spec=httpx.Response)
        r.status_code = 200
        r.raise_for_status = MagicMock()
        r.json = MagicMock(return_value={"access_token": tokens[idx["i"]], "expires_in": 1})
        idx["i"] += 1
        return r

    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        t1 = await minter.mint(audience="bsage", tenant_id="t", scope=["bsage.read"])
        t2 = await minter.mint(audience="bsage", tenant_id="t", scope=["bsage.read"])

    assert t1 == "svc-1"
    assert t2 == "svc-2"
    assert idx["i"] == 2


@pytest.mark.asyncio
async def test_minter_returns_empty_string_on_upstream_failure():
    """If BSVibe-Auth is down, ``mint`` MUST return ``""`` so the
    BaseServiceClient sends an anonymous request — the receiving
    service's auth dependency surfaces a clean 401 instead of a
    cascading 500. Pin: never propagate upstream failures past the
    minter boundary."""
    from backend.src.core.service_auth import ServiceJWTMinter

    minter = ServiceJWTMinter(
        bsvibe_auth_url="https://auth.bsvibe.dev",
        bootstrap_token_provider=lambda: "boot",
    )

    async def fake_post(self, url, **kwargs):  # noqa: ANN001
        raise httpx.ConnectError("boom")

    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        token = await minter.mint(audience="bsage", tenant_id="t", scope=["bsage.read"])

    assert token == ""


@pytest.mark.asyncio
async def test_minter_returns_empty_string_on_4xx():
    """Auth server 4xx (insufficient role, bad audience) returns "" —
    same fail-soft contract."""
    from backend.src.core.service_auth import ServiceJWTMinter

    minter = ServiceJWTMinter(
        bsvibe_auth_url="https://auth.bsvibe.dev",
        bootstrap_token_provider=lambda: "boot",
    )

    fake_resp = MagicMock(spec=httpx.Response)
    fake_resp.status_code = 403
    fake_resp.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("forbidden", request=None, response=fake_resp)
    )
    fake_resp.json = MagicMock(return_value={"error": "Insufficient role"})

    async def fake_post(self, url, **kwargs):  # noqa: ANN001
        return fake_resp

    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        token = await minter.mint(audience="bsage", tenant_id="t", scope=["bsage.read"])

    assert token == ""


@pytest.mark.asyncio
async def test_make_auth_provider_returns_callable_compatible_with_base_service_client():
    """``make_auth_provider`` returns a closure that ``BaseServiceClient``
    can call as its ``auth_provider``. The closure mints + caches.
    This is the PR's primary cross-cutting contract: with this minter
    we change the closure passed to BaseServiceClient — adapter code
    is NOT touched."""
    from backend.src.core.clients.base import BaseServiceClient
    from backend.src.core.service_auth import ServiceJWTMinter

    minter = ServiceJWTMinter(
        bsvibe_auth_url="https://auth.bsvibe.dev",
        bootstrap_token_provider=lambda: "boot",
    )

    fake_resp = MagicMock(spec=httpx.Response)
    fake_resp.status_code = 200
    fake_resp.raise_for_status = MagicMock()
    fake_resp.json = MagicMock(return_value={"access_token": "svc-from-minter", "expires_in": 600})

    captured: dict = {}

    async def post(self, url, **kwargs):  # noqa: ANN001
        return fake_resp

    async def request(self, method, url, **kwargs):  # noqa: ANN001
        captured["headers"] = kwargs.get("headers", {})
        return httpx.Response(200, json={"ok": True})

    auth_provider = minter.make_auth_provider(
        audience="bsage",
        tenant_id="11111111-1111-4111-8111-111111111111",
        scope=["bsage.read"],
    )

    client = BaseServiceClient(
        base_url="https://bsage.test",
        auth_provider=auth_provider,
        user_agent="UA",
    )

    with (
        patch.object(httpx.AsyncClient, "post", new=post),
        patch.object(httpx.AsyncClient, "request", new=request),
    ):
        await client.request("GET", "/api/health")

    assert captured["headers"]["Authorization"] == "Bearer svc-from-minter"


@pytest.mark.asyncio
async def test_concurrent_mints_for_same_tuple_dedupe_to_single_upstream_call():
    """Two coroutines mint the same (audience, tenant, scope) at the
    same time — only one upstream POST should fire. Pinning this
    contract because thundering-herd on minter-cold-start would melt
    the auth server during a deployment."""
    from backend.src.core.service_auth import ServiceJWTMinter

    minter = ServiceJWTMinter(
        bsvibe_auth_url="https://auth.bsvibe.dev",
        bootstrap_token_provider=lambda: "boot",
    )

    call_count = {"n": 0}

    async def fake_post(self, url, **kwargs):  # noqa: ANN001
        call_count["n"] += 1
        # Yield to let the second coroutine try the cache concurrently.
        await asyncio.sleep(0)
        r = MagicMock(spec=httpx.Response)
        r.status_code = 200
        r.raise_for_status = MagicMock()
        r.json = MagicMock(return_value={"access_token": "svc-once", "expires_in": 600})
        return r

    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        results = await asyncio.gather(
            minter.mint(audience="bsage", tenant_id="t", scope=["bsage.read"]),
            minter.mint(audience="bsage", tenant_id="t", scope=["bsage.read"]),
        )

    assert results == ["svc-once", "svc-once"]
    assert call_count["n"] == 1
