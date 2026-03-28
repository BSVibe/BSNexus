"""Tests for Telegram bot integration with FastAPI lifespan."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


async def test_lifespan_creates_telegram_bot():
    """Lifespan creates TelegramBot and stores it in app.state."""
    from backend.src.main import lifespan

    mock_app = MagicMock()
    mock_app.state = MagicMock()
    mock_redis = AsyncMock()
    mock_stream_manager = AsyncMock()
    mock_bot = AsyncMock()
    mock_bot.start = AsyncMock()
    mock_bot.stop = AsyncMock()

    with (
        patch("backend.src.main.init_db", new_callable=AsyncMock),
        patch("backend.src.main.get_redis", new_callable=AsyncMock, return_value=mock_redis),
        patch("backend.src.main.RedisStreamManager", return_value=mock_stream_manager),
        patch("backend.src.main.start_background_consumer", new_callable=AsyncMock),
        patch("backend.src.main.close_redis", new_callable=AsyncMock),
        patch("backend.src.main.TelegramBot", return_value=mock_bot) as mock_cls,
    ):
        async with lifespan(mock_app):
            mock_cls.assert_called_once()
            assert mock_app.state.telegram_bot == mock_bot
            mock_bot.start.assert_awaited_once()

        mock_bot.stop.assert_awaited_once()


async def test_lifespan_telegram_bot_stops_on_shutdown():
    """Telegram bot is stopped during lifespan shutdown."""
    from backend.src.main import lifespan

    mock_app = MagicMock()
    mock_app.state = MagicMock()
    mock_redis = AsyncMock()
    mock_stream_manager = AsyncMock()
    mock_bot = AsyncMock()
    mock_bot.start = AsyncMock()
    mock_bot.stop = AsyncMock()

    with (
        patch("backend.src.main.init_db", new_callable=AsyncMock),
        patch("backend.src.main.get_redis", new_callable=AsyncMock, return_value=mock_redis),
        patch("backend.src.main.RedisStreamManager", return_value=mock_stream_manager),
        patch("backend.src.main.start_background_consumer", new_callable=AsyncMock),
        patch("backend.src.main.close_redis", new_callable=AsyncMock) as mock_close_redis,
        patch("backend.src.main.TelegramBot", return_value=mock_bot),
    ):
        async with lifespan(mock_app):
            pass

        # Bot stopped before redis closed
        mock_bot.stop.assert_awaited_once()
        mock_close_redis.assert_awaited_once()
