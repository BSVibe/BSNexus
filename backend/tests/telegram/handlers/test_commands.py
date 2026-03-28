"""Tests for status command handlers — TDD: written before implementation."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.src.models import SuggestionStatus, TaskStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def mock_db_session():
    """Create a mock async DB session."""
    session = AsyncMock()
    session.commit = AsyncMock()
    session.execute = AsyncMock()
    return session


def _make_session_factory(mock_session: AsyncMock) -> MagicMock:
    """Create a mock async_sessionmaker that works with ``async with factory() as s:``."""
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=ctx)
    return factory


def _make_update_with_message() -> MagicMock:
    """Create a mock Update with a message that can reply."""
    message = AsyncMock()
    message.reply_text = AsyncMock()
    update = MagicMock()
    update.message = message
    return update


# ---------------------------------------------------------------------------
# /status command
# ---------------------------------------------------------------------------


class TestStatusCommand:
    """Test /status command handler."""

    async def test_status_returns_korean_message(self, mock_db_session, project_id):
        from backend.src.telegram.handlers.commands import CommandsHandler

        # Mock: 3 active tasks, 2 pending suggestions
        result_tasks = MagicMock()
        result_tasks.scalar_one.return_value = 3
        result_suggestions = MagicMock()
        result_suggestions.scalar_one.return_value = 2

        mock_db_session.execute = AsyncMock(side_effect=[result_tasks, result_suggestions])

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
        assert "프로젝트 상태" in msg
        assert "3" in msg
        assert "2" in msg

    async def test_status_shows_active_tasks_label(self, mock_db_session, project_id):
        from backend.src.telegram.handlers.commands import CommandsHandler

        result_tasks = MagicMock()
        result_tasks.scalar_one.return_value = 5
        result_suggestions = MagicMock()
        result_suggestions.scalar_one.return_value = 0

        mock_db_session.execute = AsyncMock(side_effect=[result_tasks, result_suggestions])

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
        assert "진행 중인 작업" in msg

    async def test_status_shows_pending_suggestions_label(self, mock_db_session, project_id):
        from backend.src.telegram.handlers.commands import CommandsHandler

        result_tasks = MagicMock()
        result_tasks.scalar_one.return_value = 0
        result_suggestions = MagicMock()
        result_suggestions.scalar_one.return_value = 4

        mock_db_session.execute = AsyncMock(side_effect=[result_tasks, result_suggestions])

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
        assert "대기 중인 제안" in msg


# ---------------------------------------------------------------------------
# /plan command
# ---------------------------------------------------------------------------


class TestPlanCommand:
    """Test /plan command handler."""

    async def test_plan_calls_planner_service(self, mock_db_session, project_id):
        from backend.src.telegram.handlers.commands import CommandsHandler

        mock_planner = AsyncMock()
        suggestion = MagicMock()
        suggestion.id = uuid.uuid4()
        suggestion.title = "테스트 작업"
        suggestion.task_type = "feature"
        suggestion.priority = 1
        suggestion.estimated_effort = "2h"
        suggestion.reasoning = "중요한 작업"
        mock_planner.generate_daily_plan = AsyncMock(return_value=[suggestion])

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

        mock_planner.generate_daily_plan.assert_awaited_once()

    async def test_plan_sends_briefing_with_keyboard(self, mock_db_session, project_id):
        from backend.src.telegram.handlers.commands import CommandsHandler

        mock_planner = AsyncMock()
        suggestion = MagicMock()
        suggestion.id = uuid.uuid4()
        suggestion.title = "테스트 작업"
        suggestion.task_type = "feature"
        suggestion.priority = 1
        suggestion.estimated_effort = "2h"
        suggestion.reasoning = "중요한 작업"
        mock_planner.generate_daily_plan = AsyncMock(return_value=[suggestion])

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

        update.message.reply_text.assert_awaited()
        call_kwargs = update.message.reply_text.call_args
        msg = call_kwargs[0][0]
        assert "오늘의 작업 제안" in msg
        # Should include reply_markup with inline keyboard
        assert call_kwargs[1]["reply_markup"] is not None

    async def test_plan_empty_suggestions(self, mock_db_session, project_id):
        from backend.src.telegram.handlers.commands import CommandsHandler

        mock_planner = AsyncMock()
        mock_planner.generate_daily_plan = AsyncMock(return_value=[])

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

        msg = update.message.reply_text.call_args[0][0]
        assert "제안된 작업이 없습니다" in msg

    async def test_plan_sends_generating_message_first(self, mock_db_session, project_id):
        from backend.src.telegram.handlers.commands import CommandsHandler

        mock_planner = AsyncMock()
        mock_planner.generate_daily_plan = AsyncMock(return_value=[])

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

        # First call should be the "generating" message
        first_call = update.message.reply_text.call_args_list[0]
        assert "생성 중" in first_call[0][0]


# ---------------------------------------------------------------------------
# /cost command
# ---------------------------------------------------------------------------


class TestCostCommand:
    """Test /cost command handler."""

    async def test_cost_returns_korean_message(self, mock_db_session, project_id):
        from backend.src.telegram.handlers.commands import CommandsHandler

        session_factory = _make_session_factory(mock_db_session)
        handler = CommandsHandler(
            db_session_factory=session_factory,
            planner_service=MagicMock(),
            chat_id="123",
            project_id=str(project_id),
        )

        update = _make_update_with_message()
        context = MagicMock()

        await handler.handle_cost(update, context)

        msg = update.message.reply_text.call_args[0][0]
        assert "비용" in msg

    async def test_cost_shows_placeholder_message(self, mock_db_session, project_id):
        from backend.src.telegram.handlers.commands import CommandsHandler

        session_factory = _make_session_factory(mock_db_session)
        handler = CommandsHandler(
            db_session_factory=session_factory,
            planner_service=MagicMock(),
            chat_id="123",
            project_id=str(project_id),
        )

        update = _make_update_with_message()
        context = MagicMock()

        await handler.handle_cost(update, context)

        msg = update.message.reply_text.call_args[0][0]
        # Should mention that cost tracking is not yet connected
        assert "연결" in msg or "준비" in msg or "아직" in msg


# ---------------------------------------------------------------------------
# CommandsHandler initialization
# ---------------------------------------------------------------------------


class TestCommandsHandlerInit:
    """Test CommandsHandler initialization."""

    def test_init_stores_dependencies(self, mock_db_session, project_id):
        from backend.src.telegram.handlers.commands import CommandsHandler

        session_factory = _make_session_factory(mock_db_session)
        planner = MagicMock()
        handler = CommandsHandler(
            db_session_factory=session_factory,
            planner_service=planner,
            chat_id="123",
            project_id=str(project_id),
        )

        assert handler.db_session_factory is session_factory
        assert handler.planner_service is planner
        assert handler.chat_id == "123"
        assert handler.project_id == str(project_id)
