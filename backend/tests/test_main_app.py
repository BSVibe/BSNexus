"""Tests for main.py app endpoints and infrastructure functions."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from httpx import AsyncClient

from backend.src.queue.background import start_background_consumer
from backend.src.storage.database import init_db


async def test_health_endpoint(client: AsyncClient) -> None:
    """GET /health returns 200 with status."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


async def test_health_deps_connected(client: AsyncClient) -> None:
    """GET /health/deps returns connected status with mocked services."""
    mock_redis = AsyncMock()
    mock_redis.ping = AsyncMock()

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock()
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_cm.__aexit__ = AsyncMock(return_value=None)
    mock_engine = MagicMock()
    mock_engine.connect.return_value = mock_cm

    with (
        patch("backend.src.main.get_redis", return_value=mock_redis),
        patch("backend.src.main.engine", mock_engine),
    ):
        response = await client.get("/health/deps")

    assert response.status_code == 200
    data = response.json()
    assert data["redis"] == "connected"
    assert data["postgresql"] == "connected"


async def test_health_deps_disconnected(client: AsyncClient) -> None:
    """GET /health/deps returns disconnected status when services are unavailable."""
    mock_engine = MagicMock()
    mock_engine.connect.side_effect = Exception("PostgreSQL unavailable")

    with (
        patch("backend.src.main.get_redis", side_effect=ConnectionError("Redis unavailable")),
        patch("backend.src.main.engine", mock_engine),
    ):
        response = await client.get("/health/deps")

    assert response.status_code == 200
    data = response.json()
    assert data["redis"] == "disconnected"
    assert data["postgresql"] == "disconnected"


async def test_start_background_consumer_sets_orchestrators() -> None:
    """start_background_consumer initializes app.state.orchestrators."""
    from fastapi import FastAPI

    app = FastAPI()
    await start_background_consumer(app)
    assert app.state.orchestrators == {}


async def test_init_db_is_noop() -> None:
    """init_db completes without error."""
    await init_db()  # Just verifies it doesn't raise


# -- CORS tests ----------------------------------------------------------------


async def test_cors_preflight(client: AsyncClient) -> None:
    """OPTIONS request returns CORS headers."""
    response = await client.options(
        "/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert "access-control-allow-origin" in response.headers


async def test_cors_origin_reflected(client: AsyncClient) -> None:
    """GET with Origin header returns Access-Control-Allow-Origin."""
    response = await client.get(
        "/health",
        headers={"Origin": "http://localhost:3000"},
    )
    assert response.status_code == 200
    assert "access-control-allow-origin" in response.headers


# -- Router registration tests ------------------------------------------------


async def test_api_routers_registered(client: AsyncClient) -> None:
    """Verify key API route prefixes are reachable (not 404 Method Not Allowed is OK)."""
    # These paths should resolve to a router (may return 405 or other status, but not 404)
    for path in ["/api/v1/projects", "/api/v1/tasks/"]:
        response = await client.get(path)
        assert response.status_code != 404, f"{path} returned 404 — router not registered"


async def test_redirect_slashes_disabled(client: AsyncClient) -> None:
    """App has redirect_slashes=False — trailing slash should not auto-redirect."""
    response = await client.get("/health/")
    # With redirect_slashes=False, /health/ should 404 (only /health is defined)
    assert response.status_code == 404


# -- Security headers middleware -----------------------------------------------


async def test_security_headers_present(client: AsyncClient) -> None:
    """Verify security headers middleware adds expected headers."""
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("x-frame-options") == "DENY"
