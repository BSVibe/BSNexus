"""Tests for auth API endpoints (callback, refresh, me, logout)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_auth_callback_redirects_to_frontend(client: AsyncClient):
    """GET /auth/callback redirects to frontend with tokens in fragment."""
    resp = await client.get(
        "/auth/callback",
        params={
            "access_token": "eyJ-access",
            "refresh_token": "eyJ-refresh",
            "state": "abc123",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith("http://localhost:3000/auth/callback#")
    assert "access_token=eyJ-access" in location
    assert "refresh_token=eyJ-refresh" in location
    assert "state=abc123" in location


async def test_auth_callback_missing_token_returns_422(client: AsyncClient):
    """GET /auth/callback without required params returns 422."""
    resp = await client.get("/auth/callback")
    assert resp.status_code == 422


async def test_auth_callback_empty_state(client: AsyncClient):
    """GET /auth/callback with empty state still works."""
    resp = await client.get(
        "/auth/callback",
        params={"access_token": "tok", "refresh_token": "ref"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "state=" in resp.headers["location"]


async def test_refresh_success(client: AsyncClient):
    """POST /api/v1/auth/refresh returns new tokens."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "access_token": "eyJ-new-access",
        "refresh_token": "eyJ-new-refresh",
        "expires_in": 3600,
    }

    with patch("backend.src.api.auth.httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_client

        resp = await client.post("/api/v1/auth/refresh", json={
            "refresh_token": "old-refresh-token",
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data["access_token"] == "eyJ-new-access"


async def test_refresh_invalid_token(client: AsyncClient):
    """POST /api/v1/auth/refresh returns 401 on invalid refresh token."""
    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.json.return_value = {"error": "invalid_grant"}

    with patch("backend.src.api.auth.httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_client

        resp = await client.post("/api/v1/auth/refresh", json={
            "refresh_token": "expired-token",
        })

    assert resp.status_code == 401


async def test_get_me(client: AsyncClient):
    """GET /api/v1/auth/me returns current user info."""
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "test-user-id"
    assert data["role"] == "admin"


async def test_logout(client: AsyncClient):
    """POST /api/v1/auth/logout returns 204."""
    with patch("backend.src.api.auth.settings") as mock_settings:
        mock_settings.supabase_service_role_key = ""
        resp = await client.post("/api/v1/auth/logout")

    assert resp.status_code == 204


async def test_logout_with_service_key(client: AsyncClient):
    """POST /api/v1/auth/logout calls Supabase when service key is set."""
    mock_response = MagicMock()
    mock_response.status_code = 200

    with patch("backend.src.api.auth.settings") as mock_settings, \
         patch("backend.src.api.auth.httpx.AsyncClient") as MockClient:
        mock_settings.supabase_service_role_key = "svc-key"
        mock_settings.supabase_url = "https://test.supabase.co"

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_client

        resp = await client.post("/api/v1/auth/logout")

    assert resp.status_code == 204
