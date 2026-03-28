"""Tests for telegram bot module — setup, configuration, and /start command."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Settings tests
# ---------------------------------------------------------------------------


class TestTelegramSettings:
    """Verify TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are in Settings."""

    def test_settings_has_telegram_bot_token(self):
        from backend.src.config import Settings

        s = Settings(
            database_url="sqlite+aiosqlite://",
            telegram_bot_token="test-token-123",
        )
        assert s.telegram_bot_token == "test-token-123"

    def test_settings_has_telegram_chat_id(self):
        from backend.src.config import Settings

        s = Settings(
            database_url="sqlite+aiosqlite://",
            telegram_chat_id="12345",
        )
        assert s.telegram_chat_id == "12345"

    def test_settings_telegram_defaults_empty(self):
        from backend.src.config import Settings

        s = Settings(database_url="sqlite+aiosqlite://")
        assert s.telegram_bot_token == ""
        assert s.telegram_chat_id == ""


# ---------------------------------------------------------------------------
# TelegramBot class tests
# ---------------------------------------------------------------------------


class TestTelegramBot:
    """Test the TelegramBot wrapper class."""

    def test_init_stores_token_and_chat_id(self):
        from backend.src.telegram.bot import TelegramBot

        bot = TelegramBot(token="tok-123", chat_id="456")
        assert bot.token == "tok-123"
        assert bot.chat_id == "456"

    def test_init_creates_application(self):
        from backend.src.telegram.bot import TelegramBot

        with patch("backend.src.telegram.bot.ApplicationBuilder") as mock_builder:
            mock_app = MagicMock()
            mock_builder.return_value.token.return_value.build.return_value = mock_app

            bot = TelegramBot(token="tok-123", chat_id="456")

            mock_builder.return_value.token.assert_called_once_with("tok-123")
            assert bot.application is mock_app

    def test_start_command_registered(self):
        from backend.src.telegram.bot import TelegramBot

        with patch("backend.src.telegram.bot.ApplicationBuilder") as mock_builder:
            mock_app = MagicMock()
            mock_builder.return_value.token.return_value.build.return_value = mock_app

            TelegramBot(token="tok-123", chat_id="456")

            # Verify at least one handler was added (the /start command)
            assert mock_app.add_handler.called

    async def test_start_handler_responds_korean(self):
        """The /start handler should respond with a Korean greeting."""
        from backend.src.telegram.bot import TelegramBot

        with patch("backend.src.telegram.bot.ApplicationBuilder") as mock_builder:
            mock_app = MagicMock()
            mock_builder.return_value.token.return_value.build.return_value = mock_app

            TelegramBot(token="tok-123", chat_id="456")

            # Extract the registered handler's callback
            handler_call = mock_app.add_handler.call_args_list[0]
            handler = handler_call[0][0]
            callback = handler.callback

            # Mock update and context
            update = MagicMock()
            update.effective_chat.id = 456
            update.message.reply_text = AsyncMock()

            context = MagicMock()

            await callback(update, context)

            update.message.reply_text.assert_called_once()
            msg = update.message.reply_text.call_args[0][0]
            # Message must be in Korean
            assert any(char >= "\uac00" and char <= "\ud7a3" for char in msg), (
                f"Expected Korean text, got: {msg}"
            )

    async def test_start_method_initializes_and_starts_polling(self):
        from backend.src.telegram.bot import TelegramBot

        with patch("backend.src.telegram.bot.ApplicationBuilder") as mock_builder:
            mock_app = AsyncMock()
            mock_app.add_handler = MagicMock()
            mock_builder.return_value.token.return_value.build.return_value = mock_app

            bot = TelegramBot(token="tok-123", chat_id="456")
            await bot.start()

            mock_app.initialize.assert_awaited_once()
            mock_app.start.assert_awaited_once()
            mock_app.updater.start_polling.assert_awaited_once()

    async def test_stop_method_shuts_down_application(self):
        from backend.src.telegram.bot import TelegramBot

        with patch("backend.src.telegram.bot.ApplicationBuilder") as mock_builder:
            mock_app = AsyncMock()
            mock_app.add_handler = MagicMock()
            mock_builder.return_value.token.return_value.build.return_value = mock_app

            bot = TelegramBot(token="tok-123", chat_id="456")
            await bot.stop()

            mock_app.updater.stop.assert_awaited_once()
            mock_app.stop.assert_awaited_once()
            mock_app.shutdown.assert_awaited_once()

    def test_bot_is_disabled_when_no_token(self):
        from backend.src.telegram.bot import TelegramBot

        bot = TelegramBot(token="", chat_id="456")
        assert bot.is_enabled is False

    def test_bot_is_enabled_when_token_provided(self):
        from backend.src.telegram.bot import TelegramBot

        with patch("backend.src.telegram.bot.ApplicationBuilder") as mock_builder:
            mock_app = MagicMock()
            mock_builder.return_value.token.return_value.build.return_value = mock_app

            bot = TelegramBot(token="tok-123", chat_id="456")
            assert bot.is_enabled is True

    async def test_start_skipped_when_disabled(self):
        from backend.src.telegram.bot import TelegramBot

        bot = TelegramBot(token="", chat_id="456")
        # Should not raise, just no-op
        await bot.start()

    async def test_stop_skipped_when_disabled(self):
        from backend.src.telegram.bot import TelegramBot

        bot = TelegramBot(token="", chat_id="456")
        # Should not raise, just no-op
        await bot.stop()


# ---------------------------------------------------------------------------
# Package structure tests
# ---------------------------------------------------------------------------


class TestTelegramPackage:
    """Verify the telegram package is correctly structured."""

    def test_telegram_package_importable(self):
        import backend.src.telegram  # noqa: F401

    def test_bot_module_importable(self):
        from backend.src.telegram.bot import TelegramBot  # noqa: F401

    def test_telegram_init_exports_bot(self):
        from backend.src.telegram import TelegramBot  # noqa: F401
