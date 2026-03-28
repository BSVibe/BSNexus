"""Tests for error handling and graceful degradation — TDD: written before implementation."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.src.models import SuggestionStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def mock_db_session():
    session = AsyncMock()
    session.commit = AsyncMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    return session


def _make_session_factory(mock_session: AsyncMock) -> MagicMock:
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=ctx)
    return factory


def _make_update_with_message() -> MagicMock:
    message = AsyncMock()
    message.reply_text = AsyncMock()
    update = MagicMock()
    update.message = message
    return update


def _make_callback_query(data: str) -> MagicMock:
    query = AsyncMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    update.message = None
    return update


def _make_suggestion(suggestion_id: uuid.UUID, project_id: uuid.UUID) -> MagicMock:
    s = MagicMock()
    s.id = suggestion_id
    s.project_id = project_id
    s.title = "테스트 작업"
    s.description = "설명"
    s.task_type = "feature"
    s.priority = 1
    s.estimated_effort = "2h"
    s.reasoning = "이유"
    s.status = SuggestionStatus.pending
    s.rejection_reason = None
    return s


# ---------------------------------------------------------------------------
# CommandsHandler error handling
# ---------------------------------------------------------------------------


class TestCommandsHandlerErrors:
    """Test that CommandsHandler sends Korean error messages on failures."""

    async def test_status_db_error_sends_korean_error(self, mock_db_session, project_id):
        from backend.src.telegram.handlers.commands import CommandsHandler

        mock_db_session.execute = AsyncMock(side_effect=Exception("DB connection lost"))
        session_factory = _make_session_factory(mock_db_session)
        handler = CommandsHandler(
            db_session_factory=session_factory,
            planner_service=MagicMock(),
            chat_id="123",
            project_id=str(project_id),
        )

        update = _make_update_with_message()
        context = MagicMock()

        await handler.handle_status(update, context)

        msg = update.message.reply_text.call_args[0][0]
        assert "오류" in msg

    async def test_plan_planner_error_sends_korean_error(self, mock_db_session, project_id):
        from backend.src.telegram.handlers.commands import CommandsHandler

        mock_planner = AsyncMock()
        mock_planner.generate_daily_plan = AsyncMock(side_effect=Exception("LLM timeout"))
        session_factory = _make_session_factory(mock_db_session)
        handler = CommandsHandler(
            db_session_factory=session_factory,
            planner_service=mock_planner,
            chat_id="123",
            project_id=str(project_id),
        )

        update = _make_update_with_message()
        context = MagicMock()

        await handler.handle_plan(update, context)

        # Should have sent a "generating" message then an error message
        calls = update.message.reply_text.call_args_list
        error_msg = calls[-1][0][0]
        assert "오류" in error_msg

    async def test_plan_error_does_not_crash(self, mock_db_session, project_id):
        """Ensure /plan doesn't raise even when planner fails."""
        from backend.src.telegram.handlers.commands import CommandsHandler

        mock_planner = AsyncMock()
        mock_planner.generate_daily_plan = AsyncMock(side_effect=RuntimeError("boom"))
        session_factory = _make_session_factory(mock_db_session)
        handler = CommandsHandler(
            db_session_factory=session_factory,
            planner_service=mock_planner,
            chat_id="123",
            project_id=str(project_id),
        )

        update = _make_update_with_message()
        context = MagicMock()

        # Should NOT raise
        await handler.handle_plan(update, context)

    async def test_status_error_does_not_crash(self, mock_db_session, project_id):
        from backend.src.telegram.handlers.commands import CommandsHandler

        mock_db_session.execute = AsyncMock(side_effect=RuntimeError("boom"))
        session_factory = _make_session_factory(mock_db_session)
        handler = CommandsHandler(
            db_session_factory=session_factory,
            planner_service=MagicMock(),
            chat_id="123",
            project_id=str(project_id),
        )

        update = _make_update_with_message()
        context = MagicMock()

        await handler.handle_status(update, context)


# ---------------------------------------------------------------------------
# BriefingHandler error handling
# ---------------------------------------------------------------------------


class TestBriefingHandlerErrors:
    """Test that BriefingHandler handles PlannerService errors gracefully."""

    async def test_send_briefing_planner_error_sends_korean_error(self):
        from backend.src.telegram.handlers.briefing import BriefingHandler

        mock_planner = AsyncMock()
        mock_planner.generate_daily_plan = AsyncMock(side_effect=Exception("LLM failed"))
        mock_session = AsyncMock()
        session_factory = _make_session_factory(mock_session)
        mock_bot = AsyncMock()

        handler = BriefingHandler(
            planner_service=mock_planner,
            db_session_factory=session_factory,
            chat_id="12345",
            project_id="test-project-id",
        )

        await handler.send_briefing(mock_bot)

        mock_bot.send_message.assert_awaited_once()
        msg = mock_bot.send_message.call_args.kwargs["text"]
        assert "오류" in msg

    async def test_send_briefing_error_does_not_crash(self):
        from backend.src.telegram.handlers.briefing import BriefingHandler

        mock_planner = AsyncMock()
        mock_planner.generate_daily_plan = AsyncMock(side_effect=RuntimeError("boom"))
        mock_session = AsyncMock()
        session_factory = _make_session_factory(mock_session)
        mock_bot = AsyncMock()

        handler = BriefingHandler(
            planner_service=mock_planner,
            db_session_factory=session_factory,
            chat_id="12345",
            project_id="test-project-id",
        )

        # Should NOT raise
        await handler.send_briefing(mock_bot)


# ---------------------------------------------------------------------------
# ApprovalHandler error handling
# ---------------------------------------------------------------------------


class TestApprovalHandlerErrors:
    """Test that ApprovalHandler handles DB errors gracefully."""

    async def test_approve_db_error_sends_korean_error(self, mock_db_session):
        from backend.src.telegram.handlers.approval import ApprovalHandler

        mock_db_session.execute = AsyncMock(side_effect=Exception("DB down"))
        session_factory = _make_session_factory(mock_db_session)
        handler = ApprovalHandler(db_session_factory=session_factory)

        suggestion_id = uuid.uuid4()
        update = _make_callback_query(f"approve:{suggestion_id}")
        context = MagicMock()

        await handler.handle_callback(update, context)

        query = update.callback_query
        msg = query.edit_message_text.call_args[0][0]
        assert "오류" in msg

    async def test_reject_db_error_sends_korean_error(self, mock_db_session):
        from backend.src.telegram.handlers.approval import ApprovalHandler

        mock_db_session.execute = AsyncMock(side_effect=Exception("DB down"))
        session_factory = _make_session_factory(mock_db_session)
        handler = ApprovalHandler(db_session_factory=session_factory)

        suggestion_id = uuid.uuid4()
        update = _make_callback_query(f"reject:{suggestion_id}")
        context = MagicMock()
        context.user_data = {}

        await handler.handle_callback(update, context)

        query = update.callback_query
        msg = query.edit_message_text.call_args[0][0]
        assert "오류" in msg

    async def test_rejection_reason_db_error_sends_korean_error(self, mock_db_session):
        from backend.src.telegram.handlers.approval import ApprovalHandler

        mock_db_session.execute = AsyncMock(side_effect=Exception("DB down"))
        session_factory = _make_session_factory(mock_db_session)
        handler = ApprovalHandler(db_session_factory=session_factory)

        message = AsyncMock()
        message.text = "사유입니다"
        message.reply_text = AsyncMock()
        update = MagicMock()
        update.message = message

        context = MagicMock()
        context.user_data = {"pending_rejection_id": str(uuid.uuid4())}

        result = await handler.handle_rejection_reason(update, context)

        assert result is True
        msg = update.message.reply_text.call_args[0][0]
        assert "오류" in msg

    async def test_approval_error_does_not_crash(self, mock_db_session):
        from backend.src.telegram.handlers.approval import ApprovalHandler

        mock_db_session.execute = AsyncMock(side_effect=RuntimeError("boom"))
        session_factory = _make_session_factory(mock_db_session)
        handler = ApprovalHandler(db_session_factory=session_factory)

        suggestion_id = uuid.uuid4()
        update = _make_callback_query(f"approve:{suggestion_id}")
        context = MagicMock()

        # Should NOT raise
        await handler.handle_callback(update, context)


# ---------------------------------------------------------------------------
# TelegramBot start/stop error handling
# ---------------------------------------------------------------------------


class TestBotLifecycleErrors:
    """Test that bot start/stop failures don't crash the application."""

    async def test_start_failure_does_not_raise(self):
        from backend.src.telegram.bot import TelegramBot

        with patch("backend.src.telegram.bot.ApplicationBuilder") as mock_builder:
            mock_app = AsyncMock()
            mock_app.add_handler = MagicMock()
            mock_app.initialize = AsyncMock(side_effect=Exception("Network error"))
            mock_builder.return_value.token.return_value.build.return_value = mock_app

            bot = TelegramBot(token="tok-123", chat_id="456")
            # Should NOT raise
            await bot.start()

    async def test_start_failure_logs_error(self):
        from backend.src.telegram.bot import TelegramBot

        with patch("backend.src.telegram.bot.ApplicationBuilder") as mock_builder:
            mock_app = AsyncMock()
            mock_app.add_handler = MagicMock()
            mock_app.initialize = AsyncMock(side_effect=Exception("Network error"))
            mock_builder.return_value.token.return_value.build.return_value = mock_app

            bot = TelegramBot(token="tok-123", chat_id="456")

            with patch("backend.src.telegram.bot.logger") as mock_logger:
                await bot.start()
                mock_logger.error.assert_called()

    async def test_stop_failure_does_not_raise(self):
        from backend.src.telegram.bot import TelegramBot

        with patch("backend.src.telegram.bot.ApplicationBuilder") as mock_builder:
            mock_app = AsyncMock()
            mock_app.add_handler = MagicMock()
            mock_app.updater.stop = AsyncMock(side_effect=Exception("Already stopped"))
            mock_builder.return_value.token.return_value.build.return_value = mock_app

            bot = TelegramBot(token="tok-123", chat_id="456")
            # Should NOT raise
            await bot.stop()

    async def test_stop_failure_logs_error(self):
        from backend.src.telegram.bot import TelegramBot

        with patch("backend.src.telegram.bot.ApplicationBuilder") as mock_builder:
            mock_app = AsyncMock()
            mock_app.add_handler = MagicMock()
            mock_app.updater.stop = AsyncMock(side_effect=Exception("Already stopped"))
            mock_builder.return_value.token.return_value.build.return_value = mock_app

            bot = TelegramBot(token="tok-123", chat_id="456")

            with patch("backend.src.telegram.bot.logger") as mock_logger:
                await bot.stop()
                mock_logger.error.assert_called()


# ---------------------------------------------------------------------------
# Lifespan error handling
# ---------------------------------------------------------------------------


class TestLifespanErrorHandling:
    """Test that bot startup failure doesn't prevent FastAPI from starting."""

    async def test_lifespan_continues_when_bot_start_fails(self):
        from backend.src.main import lifespan

        mock_app = MagicMock()
        mock_app.state = MagicMock()
        mock_redis = AsyncMock()
        mock_stream_manager = AsyncMock()

        mock_bot = AsyncMock()
        mock_bot.start = AsyncMock(side_effect=Exception("Telegram API down"))
        mock_bot.stop = AsyncMock()

        with (
            patch("backend.src.main.init_db", new_callable=AsyncMock),
            patch("backend.src.main.get_redis", new_callable=AsyncMock, return_value=mock_redis),
            patch("backend.src.main.RedisStreamManager", return_value=mock_stream_manager),
            patch("backend.src.main.start_background_consumer", new_callable=AsyncMock),
            patch("backend.src.main.close_redis", new_callable=AsyncMock),
            patch("backend.src.main.TelegramBot", return_value=mock_bot),
        ):
            # Should NOT raise — app should start even if bot fails
            async with lifespan(mock_app):
                assert mock_app.state.telegram_bot == mock_bot

    async def test_lifespan_continues_when_bot_stop_fails(self):
        from backend.src.main import lifespan

        mock_app = MagicMock()
        mock_app.state = MagicMock()
        mock_redis = AsyncMock()
        mock_stream_manager = AsyncMock()

        mock_bot = AsyncMock()
        mock_bot.start = AsyncMock()
        mock_bot.stop = AsyncMock(side_effect=Exception("Already stopped"))

        with (
            patch("backend.src.main.init_db", new_callable=AsyncMock),
            patch("backend.src.main.get_redis", new_callable=AsyncMock, return_value=mock_redis),
            patch("backend.src.main.RedisStreamManager", return_value=mock_stream_manager),
            patch("backend.src.main.start_background_consumer", new_callable=AsyncMock),
            patch("backend.src.main.close_redis", new_callable=AsyncMock) as mock_close_redis,
            patch("backend.src.main.TelegramBot", return_value=mock_bot),
        ):
            # Should NOT raise — shutdown should continue
            async with lifespan(mock_app):
                pass

            # Redis should still be closed even if bot stop fails
            mock_close_redis.assert_awaited_once()


# ---------------------------------------------------------------------------
# structlog logging verification
# ---------------------------------------------------------------------------


class TestErrorLogging:
    """Test that errors are logged with structlog."""

    async def test_commands_status_error_logged(self, mock_db_session, project_id):
        from backend.src.telegram.handlers.commands import CommandsHandler

        mock_db_session.execute = AsyncMock(side_effect=Exception("DB error"))
        session_factory = _make_session_factory(mock_db_session)
        handler = CommandsHandler(
            db_session_factory=session_factory,
            planner_service=MagicMock(),
            chat_id="123",
            project_id=str(project_id),
        )

        update = _make_update_with_message()
        context = MagicMock()

        with patch("backend.src.telegram.handlers.commands.logger") as mock_logger:
            await handler.handle_status(update, context)
            mock_logger.error.assert_called()

    async def test_briefing_error_logged(self):
        from backend.src.telegram.handlers.briefing import BriefingHandler

        mock_planner = AsyncMock()
        mock_planner.generate_daily_plan = AsyncMock(side_effect=Exception("LLM failed"))
        mock_session = AsyncMock()
        session_factory = _make_session_factory(mock_session)
        mock_bot = AsyncMock()

        handler = BriefingHandler(
            planner_service=mock_planner,
            db_session_factory=session_factory,
            chat_id="12345",
            project_id="test-project-id",
        )

        with patch("backend.src.telegram.handlers.briefing.logger") as mock_logger:
            await handler.send_briefing(mock_bot)
            mock_logger.error.assert_called()

    async def test_approval_error_logged(self, mock_db_session):
        from backend.src.telegram.handlers.approval import ApprovalHandler

        mock_db_session.execute = AsyncMock(side_effect=Exception("DB error"))
        session_factory = _make_session_factory(mock_db_session)
        handler = ApprovalHandler(db_session_factory=session_factory)

        suggestion_id = uuid.uuid4()
        update = _make_callback_query(f"approve:{suggestion_id}")
        context = MagicMock()

        with patch("backend.src.telegram.handlers.approval.logger") as mock_logger:
            await handler.handle_callback(update, context)
            mock_logger.error.assert_called()
