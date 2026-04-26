"""S4 — Auth API endpoint coverage gap (callback / refresh / me / logout).

The auth router (``api/auth.py``) is at 65% coverage — refresh,
callback, me, and logout paths weren't covered. Audit §6 calls out
'auth flow regression' as a coverage gap.

These tests pin:

  * ``GET /auth/callback`` builds a fragment redirect with the tokens.
  * ``POST /api/v1/auth/refresh`` returns a new token pair on success
    and 401 on AuthError.
  * ``GET /api/v1/auth/me`` returns the user's id/email/role.
  * ``POST /api/v1/auth/logout`` is best-effort: returns 204 even when
    the BSVibe-Auth call fails.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from bsvibe_auth import AuthError


@pytest.mark.asyncio
async def test_auth_callback_redirects_to_frontend_with_tokens(client) -> None:
    """The callback endpoint must surface the access/refresh tokens to
    the frontend SPA via a URL fragment (so they never hit a server
    log) — pinning the contract that bsvibe-auth → frontend handoff
    relies on."""
    resp = await client.get(
        "/auth/callback?access_token=at-1&refresh_token=rt-1&state=s-9",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert "/auth/callback#" in location
    assert "access_token=at-1" in location
    assert "refresh_token=rt-1" in location
    assert "state=s-9" in location


@pytest.mark.asyncio
async def test_refresh_returns_new_token_pair_on_success(client) -> None:
    """``POST /api/v1/auth/refresh`` exchanges a refresh token for a
    new access pair via the bsvibe-auth provider."""
    pair = SimpleNamespace(
        access_token="new-at",
        refresh_token="new-rt",
        expires_in=3600,
    )
    with patch(
        "backend.src.api.auth.auth_provider.refresh_token",
        new=AsyncMock(return_value=pair),
    ):
        resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "old-rt"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"] == "new-at"
    assert body["refresh_token"] == "new-rt"
    assert body["expires_in"] == 3600
    assert body["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_refresh_returns_401_on_invalid_refresh_token(client) -> None:
    """An ``AuthError`` from bsvibe-auth's refresh endpoint must surface
    as 401 — pin the user-visible failure shape so the SPA can drop the
    session and redirect to login instead of looping retries."""
    with patch(
        "backend.src.api.auth.auth_provider.refresh_token",
        new=AsyncMock(side_effect=AuthError("expired")),
    ):
        resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "bad"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_returns_current_user_info(client, mock_user) -> None:
    """``GET /api/v1/auth/me`` returns id/email/role for the
    authenticated user. Critical for the SPA's bootstrap render."""
    resp = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == mock_user.id
    assert body["email"] == mock_user.email
    assert body["role"] == mock_user.app_metadata.get("role")


@pytest.mark.asyncio
async def test_logout_returns_204_even_on_provider_error(client) -> None:
    """The logout call to BSVibe-Auth is best-effort. A network failure
    must NOT block the user's local session teardown — the SPA already
    deleted the cookie locally, so we always return 204."""
    with patch(
        "backend.src.api.auth.auth_provider.logout",
        new=AsyncMock(side_effect=RuntimeError("upstream down")),
    ):
        resp = await client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": "Bearer the-token"},
        )

    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_logout_calls_provider_with_token_on_happy_path(client) -> None:
    """Pin that the logout handler forwards the bearer token to
    ``auth_provider.logout`` (so the bsvibe-auth side can revoke)."""
    spy = AsyncMock(return_value=None)
    with patch("backend.src.api.auth.auth_provider.logout", new=spy):
        resp = await client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": "Bearer real-token-123"},
        )

    assert resp.status_code == 204
    spy.assert_awaited_once_with("real-token-123")


@pytest.mark.asyncio
async def test_logout_skips_provider_when_no_bearer_present(client) -> None:
    """No Authorization header → no token to forward; the handler must
    short-circuit (don't call ``auth_provider.logout`` with empty)."""
    spy = AsyncMock(return_value=None)
    with patch("backend.src.api.auth.auth_provider.logout", new=spy):
        # The endpoint requires a logged-in user (mock_user override).
        # We test the *no Bearer header on logout* case which still
        # passes the dep override but the handler's header parse runs.
        resp = await client.post("/api/v1/auth/logout")
    assert resp.status_code == 204
    spy.assert_not_awaited()
