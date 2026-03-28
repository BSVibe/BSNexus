"""Tests for signing key startup validation in lifespan."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


async def test_lifespan_raises_on_dev_key_in_production() -> None:
    """Lifespan raises RuntimeError when signing key is dev default and debug=False."""
    from backend.src.main import lifespan

    mock_app = MagicMock()
    mock_app.state = MagicMock()

    with patch("backend.src.main.app_settings") as mock_settings:
        mock_settings.debug = False
        mock_settings.prompt_signing_key = "dev-signing-key-change-in-production"

        with pytest.raises(RuntimeError, match="prompt_signing_key is still the dev default"):
            async with lifespan(mock_app):
                pass  # Should never reach here


async def test_lifespan_allows_dev_key_in_debug_mode() -> None:
    """Lifespan does NOT raise when debug=True even with dev signing key."""
    from backend.src.main import lifespan

    mock_app = MagicMock()
    mock_app.state = MagicMock()

    with (
        patch("backend.src.main.app_settings") as mock_settings,
        patch("backend.src.main.init_db", new_callable=AsyncMock),
        patch("backend.src.main.get_redis", new_callable=AsyncMock, return_value=AsyncMock()),
        patch("backend.src.main.RedisStreamManager", return_value=AsyncMock()),
        patch("backend.src.main.start_background_consumer", new_callable=AsyncMock),
        patch("backend.src.main.close_redis", new_callable=AsyncMock),
    ):
        mock_settings.debug = True
        mock_settings.prompt_signing_key = "dev-signing-key-change-in-production"

        async with lifespan(mock_app):
            pass  # Should NOT raise


async def test_lifespan_allows_custom_key_in_production() -> None:
    """Lifespan does NOT raise when signing key is a real production key."""
    from backend.src.main import lifespan

    mock_app = MagicMock()
    mock_app.state = MagicMock()

    with (
        patch("backend.src.main.app_settings") as mock_settings,
        patch("backend.src.main.init_db", new_callable=AsyncMock),
        patch("backend.src.main.get_redis", new_callable=AsyncMock, return_value=AsyncMock()),
        patch("backend.src.main.RedisStreamManager", return_value=AsyncMock()),
        patch("backend.src.main.start_background_consumer", new_callable=AsyncMock),
        patch("backend.src.main.close_redis", new_callable=AsyncMock),
    ):
        mock_settings.debug = False
        mock_settings.prompt_signing_key = "a-real-production-signing-key-abc123"

        async with lifespan(mock_app):
            pass  # Should NOT raise
