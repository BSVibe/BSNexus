"""Tests for main.py app endpoints and infrastructure functions."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from backend.src.queue.background import start_background_consumer
from backend.src.storage.database import init_db

pytestmark = pytest.mark.asyncio


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


async def test_cors_preflight_blocked_by_default(client: AsyncClient) -> None:
    """OPTIONS request is blocked when no origins are configured (secure default)."""
    response = await client.options(
        "/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    # With empty allow_origins, CORSMiddleware returns 400 for preflight
    assert response.status_code == 400


async def test_cors_origin_not_reflected_by_default(client: AsyncClient) -> None:
    """GET with Origin header does NOT reflect it when origins list is empty."""
    response = await client.get(
        "/health",
        headers={"Origin": "http://localhost:3000"},
    )
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


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


# -- Lifespan tests -----------------------------------------------------------


async def test_lifespan_startup_and_shutdown() -> None:
    """Exercise the lifespan context manager (lines 78-89)."""
    from backend.src.main import lifespan

    mock_app = MagicMock()
    mock_app.state = MagicMock()
    mock_redis = AsyncMock()
    mock_stream_manager = AsyncMock()

    with (
        patch("backend.src.main.app_settings") as mock_settings,
        patch("backend.src.main.init_db", new_callable=AsyncMock) as mock_init_db,
        patch("backend.src.main.get_redis", new_callable=AsyncMock, return_value=mock_redis),
        patch("backend.src.main.RedisStreamManager", return_value=mock_stream_manager),
        patch("backend.src.main.start_background_consumer", new_callable=AsyncMock),
        patch("backend.src.main.close_redis", new_callable=AsyncMock) as mock_close_redis,
    ):
        # Bypass signing key validation (debug mode)
        mock_settings.debug = True
        mock_settings.prompt_signing_key = "dev-signing-key-change-in-production"

        async with lifespan(mock_app):
            mock_init_db.assert_awaited_once()
            assert mock_app.state.redis == mock_redis
            assert mock_app.state.stream_manager == mock_stream_manager
            mock_stream_manager.initialize_streams.assert_awaited_once()

        mock_close_redis.assert_awaited_once()


async def test_lifespan_rejects_dev_signing_key_in_production() -> None:
    """Lifespan raises RuntimeError when prompt_signing_key is the dev default in non-debug mode."""
    from backend.src.main import lifespan

    mock_app = MagicMock()

    with patch("backend.src.main.app_settings") as mock_settings:
        mock_settings.debug = False
        mock_settings.prompt_signing_key = "dev-signing-key-change-in-production"
        mock_settings.encryption_key = "real-key"

        with pytest.raises(RuntimeError, match="prompt_signing_key"):
            async with lifespan(mock_app):
                pass


async def test_lifespan_rejects_dev_encryption_key_in_production() -> None:
    """Lifespan raises RuntimeError when encryption_key is the dev default in non-debug mode."""
    from backend.src.main import lifespan

    mock_app = MagicMock()

    with patch("backend.src.main.app_settings") as mock_settings:
        mock_settings.debug = False
        mock_settings.prompt_signing_key = "real-key"
        mock_settings.encryption_key = "dev-encryption-key-change-in-production"

        with pytest.raises(RuntimeError, match="encryption_key"):
            async with lifespan(mock_app):
                pass


# -- _setup_logging with file handlers ----------------------------------------


def test_setup_logging_with_file_handlers(tmp_path: object) -> None:
    """Exercise _setup_logging file handler code (lines 48-68) when TESTING is unset."""
    import logging
    import os

    from backend.src.main import _setup_logging

    # Clear existing handlers to test fresh
    root = logging.getLogger()
    original_handlers = root.handlers.copy()
    root.handlers.clear()

    env_backup = os.environ.pop("TESTING", None)
    try:
        with patch("backend.src.main.app_settings") as mock_settings:
            mock_settings.log_level = "INFO"
            mock_settings.log_dir = str(tmp_path)
            _setup_logging()

        # Should have console + file handler on root
        assert len(root.handlers) >= 2
        # Orchestrator logger should have its own handler
        orch_logger = logging.getLogger("backend.src.core.orchestrator")
        assert len(orch_logger.handlers) >= 1
    finally:
        root.handlers.clear()
        root.handlers.extend(original_handlers)
        logging.getLogger("backend.src.core.orchestrator").handlers.clear()
        logging.getLogger("backend.src.core.state_machine").handlers.clear()
        if env_backup is not None:
            os.environ["TESTING"] = env_backup
