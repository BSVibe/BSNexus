"""Tests for morning briefing handler — TDD: written before implementation."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.src.models import SuggestionStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_planner_service():
    """Create a mock PlannerService."""
    service = AsyncMock()
    service.generate_daily_plan = AsyncMock(return_value=[])
    return service


@pytest.fixture
def mock_db_session():
    """Create a mock async DB session."""
    session = AsyncMock()
    return session


@pytest.fixture
def sample_suggestions():
    """Create sample TaskSuggestion-like objects."""
    suggestions = []
    for i, (title, task_type, priority) in enumerate([
        ("API 엔드포인트 리팩토링", "refactor", 1),
        ("로그인 버그 수정", "bugfix", 2),
        ("테스트 커버리지 개선", "test", 3),
    ]):
        s = MagicMock()
        s.id = uuid.uuid4()
        s.title = title
        s.description = f"설명 {i}"
        s.task_type = task_type
        s.priority = priority
        s.estimated_effort = "2h"
        s.reasoning = f"이유 {i}"
        s.status = SuggestionStatus.pending
        suggestions.append(s)
    return suggestions


# ---------------------------------------------------------------------------
# BriefingHandler instantiation
# ---------------------------------------------------------------------------


class TestBriefingHandlerInit:
    """Test BriefingHandler initialization."""

    def test_init_stores_dependencies(self, mock_planner_service, mock_db_session):
        from backend.src.telegram.handlers.briefing import BriefingHandler

        handler = BriefingHandler(
            planner_service=mock_planner_service,
            db_session_factory=mock_db_session,
            chat_id="12345",
            project_id="test-project-id",
        )
        assert handler.chat_id == "12345"
        assert handler.project_id == "test-project-id"


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------


class TestBriefingMessageFormatting:
    """Test that briefing messages are formatted in Korean with inline buttons."""

    def test_format_briefing_message_korean_header(self, sample_suggestions):
        from backend.src.telegram.handlers.briefing import BriefingHandler

        text, keyboard = BriefingHandler.format_briefing(sample_suggestions)

        assert "오늘의 작업 제안" in text

    def test_format_briefing_message_contains_all_suggestions(self, sample_suggestions):
        from backend.src.telegram.handlers.briefing import BriefingHandler

        text, keyboard = BriefingHandler.format_briefing(sample_suggestions)

        for s in sample_suggestions:
            assert s.title in text

    def test_format_briefing_message_contains_task_type(self, sample_suggestions):
        from backend.src.telegram.handlers.briefing import BriefingHandler

        text, keyboard = BriefingHandler.format_briefing(sample_suggestions)

        for s in sample_suggestions:
            assert s.task_type in text

    def test_format_briefing_message_contains_priority(self, sample_suggestions):
        from backend.src.telegram.handlers.briefing import BriefingHandler

        text, keyboard = BriefingHandler.format_briefing(sample_suggestions)

        for s in sample_suggestions:
            assert str(s.priority) in text

    def test_format_briefing_inline_keyboard_buttons(self, sample_suggestions):
        from backend.src.telegram.handlers.briefing import BriefingHandler

        text, keyboard = BriefingHandler.format_briefing(sample_suggestions)

        # Each suggestion should have approve/reject buttons
        assert len(keyboard) == len(sample_suggestions)
        for row in keyboard:
            labels = [btn.text for btn in row]
            assert any("승인" in label for label in labels)
            assert any("거부" in label for label in labels)

    def test_format_briefing_callback_data_contains_suggestion_id(self, sample_suggestions):
        from backend.src.telegram.handlers.briefing import BriefingHandler

        text, keyboard = BriefingHandler.format_briefing(sample_suggestions)

        for i, row in enumerate(keyboard):
            approve_btn = row[0]
            reject_btn = row[1]
            assert str(sample_suggestions[i].id) in approve_btn.callback_data
            assert str(sample_suggestions[i].id) in reject_btn.callback_data

    def test_format_briefing_empty_suggestions(self):
        from backend.src.telegram.handlers.briefing import BriefingHandler

        text, keyboard = BriefingHandler.format_briefing([])

        assert "제안된 작업이 없습니다" in text
        assert len(keyboard) == 0

    def test_format_briefing_includes_effort(self, sample_suggestions):
        from backend.src.telegram.handlers.briefing import BriefingHandler

        text, keyboard = BriefingHandler.format_briefing(sample_suggestions)

        assert "2h" in text

    def test_format_briefing_includes_reasoning(self, sample_suggestions):
        from backend.src.telegram.handlers.briefing import BriefingHandler

        text, keyboard = BriefingHandler.format_briefing(sample_suggestions)

        for s in sample_suggestions:
            assert s.reasoning in text


# ---------------------------------------------------------------------------
# send_briefing — integration with PlannerService
# ---------------------------------------------------------------------------


def _make_session_factory(mock_session: AsyncMock) -> MagicMock:
    """Create a mock async_sessionmaker that works with ``async with factory() as s:``."""
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=ctx)
    return factory


class TestSendBriefing:
    """Test send_briefing calls PlannerService and sends formatted message."""

    async def test_send_briefing_calls_planner(self, mock_planner_service, sample_suggestions):
        from backend.src.telegram.handlers.briefing import BriefingHandler

        mock_planner_service.generate_daily_plan.return_value = sample_suggestions
        mock_session = AsyncMock()
        session_factory = _make_session_factory(mock_session)

        mock_bot = AsyncMock()

        handler = BriefingHandler(
            planner_service=mock_planner_service,
            db_session_factory=session_factory,
            chat_id="12345",
            project_id="test-project-id",
        )

        await handler.send_briefing(mock_bot)

        mock_planner_service.generate_daily_plan.assert_awaited_once_with(
            "test-project-id", mock_session
        )

    async def test_send_briefing_sends_message_to_chat(self, mock_planner_service, sample_suggestions):
        from backend.src.telegram.handlers.briefing import BriefingHandler

        mock_planner_service.generate_daily_plan.return_value = sample_suggestions
        mock_session = AsyncMock()
        session_factory = _make_session_factory(mock_session)

        mock_bot = AsyncMock()

        handler = BriefingHandler(
            planner_service=mock_planner_service,
            db_session_factory=session_factory,
            chat_id="12345",
            project_id="test-project-id",
        )

        await handler.send_briefing(mock_bot)

        mock_bot.send_message.assert_awaited_once()
        call_kwargs = mock_bot.send_message.call_args
        assert call_kwargs.kwargs["chat_id"] == "12345"
        assert "오늘의 작업 제안" in call_kwargs.kwargs["text"]

    async def test_send_briefing_empty_suggestions_sends_no_tasks_message(self, mock_planner_service):
        from backend.src.telegram.handlers.briefing import BriefingHandler

        mock_planner_service.generate_daily_plan.return_value = []
        mock_session = AsyncMock()
        session_factory = _make_session_factory(mock_session)

        mock_bot = AsyncMock()

        handler = BriefingHandler(
            planner_service=mock_planner_service,
            db_session_factory=session_factory,
            chat_id="12345",
            project_id="test-project-id",
        )

        await handler.send_briefing(mock_bot)

        call_kwargs = mock_bot.send_message.call_args
        assert "제안된 작업이 없습니다" in call_kwargs.kwargs["text"]

    async def test_send_briefing_includes_inline_keyboard(self, mock_planner_service, sample_suggestions):
        from backend.src.telegram.handlers.briefing import BriefingHandler

        mock_planner_service.generate_daily_plan.return_value = sample_suggestions
        mock_session = AsyncMock()
        session_factory = _make_session_factory(mock_session)

        mock_bot = AsyncMock()

        handler = BriefingHandler(
            planner_service=mock_planner_service,
            db_session_factory=session_factory,
            chat_id="12345",
            project_id="test-project-id",
        )

        await handler.send_briefing(mock_bot)

        call_kwargs = mock_bot.send_message.call_args
        reply_markup = call_kwargs.kwargs["reply_markup"]
        assert reply_markup is not None
        assert len(reply_markup.inline_keyboard) == len(sample_suggestions)
