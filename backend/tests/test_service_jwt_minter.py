"""Tests for the OAuth2-backed ``ServiceJWTMinter`` wrapper.

The wrapper preserves the BSNexus contract — fail-soft ``mint``,
``(audience, tenant_id, scope)`` cache shape, ``make_auth_provider``
closure for ``BaseServiceClient`` — while delegating to
``bsvibe_authz.ServiceTokenMinter`` (OAuth2 client_credentials grant
against BSVibe-Auth ``/api/oauth/token``).
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any

import httpx
import pytest

from backend.src.core.service_auth import (
    ServiceJWTMinter,
    get_service_jwt_minter,
    set_service_jwt_minter,
)


def _oauth_handler(
    *,
    response: dict[str, Any] | None = None,
    status: int = 200,
    capture: list[dict[str, Any]] | None = None,
) -> httpx.MockTransport:
    body = response or {"access_token": "tok-1", "expires_in": 3600}

    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture.append(
                {
                    "url": str(request.url),
                    "authorization": request.headers.get("authorization"),
                    "content_type": request.headers.get("content-type"),
                    "form": dict(httpx.QueryParams(request.content.decode())),
                }
            )
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


def _patch_oauth_transport(monkeypatch, transport: httpx.MockTransport) -> None:
    """Force every minter created via the wrapper to use ``transport``.

    The shared ``bsvibe_authz.ServiceTokenMinter`` accepts a transport
    kwarg; the BSNexus wrapper does not pass one through, so we monkey-
    patch its constructor to inject the mock for tests only.
    """
    import bsvibe_authz.service_token_minter as mod

    real_init = mod.ServiceTokenMinter.__init__

    def _wrapped(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("transport", transport)
        return real_init(self, *args, **kwargs)

    monkeypatch.setattr(mod.ServiceTokenMinter, "__init__", _wrapped)


@pytest.fixture
def base_kwargs() -> dict[str, Any]:
    return {
        "bsvibe_auth_url": "https://auth.bsvibe.test",
        "client_id": "bsnexus-prod",
        "client_secret": "very-long-random-secret",
    }


@pytest.mark.asyncio
async def test_mint_returns_token_via_oauth_endpoint(monkeypatch, base_kwargs):
    capture: list[dict[str, Any]] = []
    _patch_oauth_transport(monkeypatch, _oauth_handler(capture=capture))

    minter = ServiceJWTMinter(**base_kwargs)
    tok = await minter.mint(audience="sage", tenant_id="t-1", scope=["sage:read"])

    assert tok == "tok-1"
    assert len(capture) == 1
    call = capture[0]
    assert call["url"].endswith("/api/oauth/token")
    assert call["form"] == {
        "grant_type": "client_credentials",
        "audience": "sage",
        "scope": "sage:read",
    }
    decoded = base64.b64decode(call["authorization"][len("Basic ") :]).decode()
    assert decoded == "bsnexus-prod:very-long-random-secret"


@pytest.mark.asyncio
async def test_mint_caches_per_audience_and_scope(monkeypatch, base_kwargs):
    capture: list[dict[str, Any]] = []
    _patch_oauth_transport(monkeypatch, _oauth_handler(capture=capture))
    minter = ServiceJWTMinter(**base_kwargs)

    await minter.mint(audience="sage", tenant_id="t-1", scope=["sage:read"])
    await minter.mint(audience="sage", tenant_id="t-1", scope=["sage:read"])

    assert len(capture) == 1, "second call must hit the cache"


@pytest.mark.asyncio
async def test_mint_separate_cache_per_audience(monkeypatch, base_kwargs):
    capture: list[dict[str, Any]] = []
    _patch_oauth_transport(monkeypatch, _oauth_handler(capture=capture))
    minter = ServiceJWTMinter(**base_kwargs)

    await minter.mint(audience="sage", tenant_id="t-1", scope=["sage:read"])
    await minter.mint(audience="supervisor", tenant_id="t-1", scope=["supervisor:audit.write"])

    assert len(capture) == 2


@pytest.mark.asyncio
async def test_mint_separate_cache_per_scope(monkeypatch, base_kwargs):
    capture: list[dict[str, Any]] = []
    _patch_oauth_transport(monkeypatch, _oauth_handler(capture=capture))
    minter = ServiceJWTMinter(**base_kwargs)

    await minter.mint(audience="sage", tenant_id="t-1", scope=["sage:read"])
    await minter.mint(audience="sage", tenant_id="t-1", scope=["sage:write"])

    assert len(capture) == 2


@pytest.mark.asyncio
async def test_mint_returns_empty_string_on_4xx(monkeypatch, base_kwargs):
    _patch_oauth_transport(
        monkeypatch,
        _oauth_handler(response={"error": "invalid_client"}, status=401),
    )
    minter = ServiceJWTMinter(**base_kwargs)

    tok = await minter.mint(audience="sage", tenant_id="t-1", scope=["sage:read"])
    assert tok == ""


@pytest.mark.asyncio
async def test_mint_returns_empty_string_on_transport_error(monkeypatch, base_kwargs):
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns blew up")

    _patch_oauth_transport(monkeypatch, httpx.MockTransport(handler))
    minter = ServiceJWTMinter(**base_kwargs)

    tok = await minter.mint(audience="sage", tenant_id="t-1", scope=["sage:read"])
    assert tok == ""


@pytest.mark.asyncio
async def test_make_auth_provider_returns_callable(monkeypatch, base_kwargs):
    _patch_oauth_transport(monkeypatch, _oauth_handler())
    minter = ServiceJWTMinter(**base_kwargs)

    provider = minter.make_auth_provider(audience="sage", tenant_id="t-1", scope=["sage:read"])
    assert callable(provider)
    tok = await provider()
    assert tok == "tok-1"


@pytest.mark.asyncio
async def test_invalidate_drops_cache(monkeypatch, base_kwargs):
    capture: list[dict[str, Any]] = []
    _patch_oauth_transport(monkeypatch, _oauth_handler(capture=capture))
    minter = ServiceJWTMinter(**base_kwargs)

    await minter.mint(audience="sage", tenant_id="t-1", scope=["sage:read"])
    minter.invalidate(audience="sage", tenant_id="t-1", scope=["sage:read"])
    await minter.mint(audience="sage", tenant_id="t-1", scope=["sage:read"])

    assert len(capture) == 2


@pytest.mark.asyncio
async def test_concurrent_mints_dedupe_to_single_upstream_call(monkeypatch, base_kwargs):
    capture: list[dict[str, Any]] = []

    async def slow_handler(request: httpx.Request) -> httpx.Response:
        capture.append({})
        await asyncio.sleep(0.05)
        return httpx.Response(200, json={"access_token": "tok-shared", "expires_in": 3600})

    _patch_oauth_transport(monkeypatch, httpx.MockTransport(slow_handler))
    minter = ServiceJWTMinter(**base_kwargs)

    results = await asyncio.gather(
        *(minter.mint(audience="sage", tenant_id="t-1", scope=["sage:read"]) for _ in range(5))
    )
    assert all(r == "tok-shared" for r in results)
    assert len(capture) == 1


def test_init_rejects_empty_credentials():
    with pytest.raises(ValueError, match="non-empty"):
        ServiceJWTMinter(bsvibe_auth_url="https://x", client_id="", client_secret="s")
    with pytest.raises(ValueError, match="non-empty"):
        ServiceJWTMinter(bsvibe_auth_url="https://x", client_id="c", client_secret="")


def test_get_service_jwt_minter_none_when_creds_missing(monkeypatch):
    set_service_jwt_minter(None)
    from backend.src import config as cfg

    monkeypatch.setattr(cfg.settings, "bsvibe_client_id", "")
    monkeypatch.setattr(cfg.settings, "bsvibe_client_secret", "")
    assert get_service_jwt_minter() is None


def test_get_service_jwt_minter_singleton_when_creds_present(monkeypatch):
    set_service_jwt_minter(None)
    from backend.src import config as cfg

    monkeypatch.setattr(cfg.settings, "bsvibe_client_id", "bsnexus-prod")
    monkeypatch.setattr(cfg.settings, "bsvibe_client_secret", "secret")
    monkeypatch.setattr(cfg.settings, "bsvibe_auth_url", "https://auth.x")

    a = get_service_jwt_minter()
    b = get_service_jwt_minter()
    assert a is not None and a is b
    set_service_jwt_minter(None)


@pytest.mark.asyncio
async def test_mint_returns_empty_string_on_invalid_audience(monkeypatch, base_kwargs):
    """Audience the shared lib rejects at construction → fail-soft, not raise."""
    _patch_oauth_transport(monkeypatch, _oauth_handler())
    minter = ServiceJWTMinter(**base_kwargs)

    tok = await minter.mint(
        audience="not-a-real-audience",
        tenant_id="t-1",
        scope=["sage:read"],
    )
    assert tok == ""
