"""Tests for task approval handler via inline keyboard — TDD: written before implementation."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.src.models import PhaseStatus, SuggestionStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def suggestion_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def phase_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def project_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def mock_db_session():
    """Create a mock async DB session."""
    session = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.add = MagicMock()
    return session


def _make_session_factory(mock_session: AsyncMock) -> MagicMock:
    """Create a mock async_sessionmaker that works with ``async with factory() as s:``."""
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=ctx)
    return factory


def _make_suggestion(suggestion_id: uuid.UUID, project_id: uuid.UUID) -> MagicMock:
    """Create a mock TaskSuggestion."""
    s = MagicMock()
    s.id = suggestion_id
    s.project_id = project_id
    s.title = "API 엔드포인트 리팩토링"
    s.description = "API 리팩토링 설명"
    s.task_type = "refactor"
    s.priority = 2
    s.estimated_effort = "2h"
    s.reasoning = "코드 품질 개선"
    s.status = SuggestionStatus.pending
    s.rejection_reason = None
    return s


def _make_phase(phase_id: uuid.UUID, project_id: uuid.UUID) -> MagicMock:
    """Create a mock Phase."""
    p = MagicMock()
    p.id = phase_id
    p.project_id = project_id
    p.name = "Phase 1"
    p.status = PhaseStatus.active
    return p


def _make_callback_query(data: str) -> MagicMock:
    """Create a mock CallbackQuery update."""
    query = AsyncMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message = MagicMock()
    query.message.reply_text = AsyncMock()

    update = MagicMock()
    update.callback_query = query
    update.message = None
    return update


def _make_text_message(text: str) -> MagicMock:
    """Create a mock text message update."""
    message = AsyncMock()
    message.text = text
    message.reply_text = AsyncMock()

    update = MagicMock()
    update.message = message
    update.callback_query = None
    return update


# ---------------------------------------------------------------------------
# ApprovalHandler initialization
# ---------------------------------------------------------------------------


class TestApprovalHandlerInit:
    """Test ApprovalHandler initialization."""

    def test_init_stores_dependencies(self, mock_db_session):
        from backend.src.telegram.handlers.approval import ApprovalHandler

        session_factory = _make_session_factory(mock_db_session)
        handler = ApprovalHandler(db_session_factory=session_factory)

        assert handler.db_session_factory is session_factory


# ---------------------------------------------------------------------------
# Approve callback
# ---------------------------------------------------------------------------


class TestApproveCallback:
    """Test approve button callback handling."""

    async def test_approve_updates_suggestion_status(
        self, mock_db_session, suggestion_id, phase_id, project_id
    ):
        from backend.src.telegram.handlers.approval import ApprovalHandler

        suggestion = _make_suggestion(suggestion_id, project_id)
        phase = _make_phase(phase_id, project_id)

        # Mock DB query results: first call returns suggestion, second returns phase
        result_suggestion = MagicMock()
        result_suggestion.scalar_one_or_none.return_value = suggestion
        result_phase = MagicMock()
        result_phase.scalar_one_or_none.return_value = phase

        mock_db_session.execute = AsyncMock(side_effect=[result_suggestion, result_phase])

        session_factory = _make_session_factory(mock_db_session)
        handler = ApprovalHandler(db_session_factory=session_factory)

        update = _make_callback_query(f"approve:{suggestion_id}")
        context = MagicMock()

        await handler.handle_callback(update, context)

        assert suggestion.status == SuggestionStatus.approved

    async def test_approve_creates_task(
        self, mock_db_session, suggestion_id, phase_id, project_id
    ):
        from backend.src.telegram.handlers.approval import ApprovalHandler

        suggestion = _make_suggestion(suggestion_id, project_id)
        phase = _make_phase(phase_id, project_id)

        result_suggestion = MagicMock()
        result_suggestion.scalar_one_or_none.return_value = suggestion
        result_phase = MagicMock()
        result_phase.scalar_one_or_none.return_value = phase

        mock_db_session.execute = AsyncMock(side_effect=[result_suggestion, result_phase])

        session_factory = _make_session_factory(mock_db_session)
        handler = ApprovalHandler(db_session_factory=session_factory)

        update = _make_callback_query(f"approve:{suggestion_id}")
        context = MagicMock()

        await handler.handle_callback(update, context)

        mock_db_session.add.assert_called_once()
        mock_db_session.commit.assert_awaited_once()

    async def test_approve_sends_korean_confirmation(
        self, mock_db_session, suggestion_id, phase_id, project_id
    ):
        from backend.src.telegram.handlers.approval import ApprovalHandler

        suggestion = _make_suggestion(suggestion_id, project_id)
        phase = _make_phase(phase_id, project_id)

        result_suggestion = MagicMock()
        result_suggestion.scalar_one_or_none.return_value = suggestion
        result_phase = MagicMock()
        result_phase.scalar_one_or_none.return_value = phase

        mock_db_session.execute = AsyncMock(side_effect=[result_suggestion, result_phase])

        session_factory = _make_session_factory(mock_db_session)
        handler = ApprovalHandler(db_session_factory=session_factory)

        update = _make_callback_query(f"approve:{suggestion_id}")
        context = MagicMock()

        await handler.handle_callback(update, context)

        query = update.callback_query
        query.answer.assert_awaited_once()
        query.edit_message_text.assert_awaited_once()
        msg = query.edit_message_text.call_args[0][0]
        assert "승인" in msg
        assert suggestion.title in msg

    async def test_approve_suggestion_not_found_sends_error(self, mock_db_session, suggestion_id):
        from backend.src.telegram.handlers.approval import ApprovalHandler

        result_suggestion = MagicMock()
        result_suggestion.scalar_one_or_none.return_value = None

        mock_db_session.execute = AsyncMock(return_value=result_suggestion)

        session_factory = _make_session_factory(mock_db_session)
        handler = ApprovalHandler(db_session_factory=session_factory)

        update = _make_callback_query(f"approve:{suggestion_id}")
        context = MagicMock()

        await handler.handle_callback(update, context)

        query = update.callback_query
        query.answer.assert_awaited_once()
        msg = query.edit_message_text.call_args[0][0]
        assert "찾을 수 없습니다" in msg

    async def test_approve_no_active_phase_sends_error(
        self, mock_db_session, suggestion_id, project_id
    ):
        from backend.src.telegram.handlers.approval import ApprovalHandler

        suggestion = _make_suggestion(suggestion_id, project_id)

        result_suggestion = MagicMock()
        result_suggestion.scalar_one_or_none.return_value = suggestion
        result_phase = MagicMock()
        result_phase.scalar_one_or_none.return_value = None

        mock_db_session.execute = AsyncMock(side_effect=[result_suggestion, result_phase])

        session_factory = _make_session_factory(mock_db_session)
        handler = ApprovalHandler(db_session_factory=session_factory)

        update = _make_callback_query(f"approve:{suggestion_id}")
        context = MagicMock()

        await handler.handle_callback(update, context)

        query = update.callback_query
        msg = query.edit_message_text.call_args[0][0]
        assert "활성 페이즈" in msg

    async def test_approve_already_processed_sends_error(
        self, mock_db_session, suggestion_id, project_id
    ):
        from backend.src.telegram.handlers.approval import ApprovalHandler

        suggestion = _make_suggestion(suggestion_id, project_id)
        suggestion.status = SuggestionStatus.approved

        result_suggestion = MagicMock()
        result_suggestion.scalar_one_or_none.return_value = suggestion

        mock_db_session.execute = AsyncMock(return_value=result_suggestion)

        session_factory = _make_session_factory(mock_db_session)
        handler = ApprovalHandler(db_session_factory=session_factory)

        update = _make_callback_query(f"approve:{suggestion_id}")
        context = MagicMock()

        await handler.handle_callback(update, context)

        query = update.callback_query
        msg = query.edit_message_text.call_args[0][0]
        assert "이미 처리" in msg


# ---------------------------------------------------------------------------
# Reject callback
# ---------------------------------------------------------------------------


class TestRejectCallback:
    """Test reject button callback handling."""

    async def test_reject_asks_for_reason(self, mock_db_session, suggestion_id, project_id):
        from backend.src.telegram.handlers.approval import ApprovalHandler

        suggestion = _make_suggestion(suggestion_id, project_id)

        result_suggestion = MagicMock()
        result_suggestion.scalar_one_or_none.return_value = suggestion

        mock_db_session.execute = AsyncMock(return_value=result_suggestion)

        session_factory = _make_session_factory(mock_db_session)
        handler = ApprovalHandler(db_session_factory=session_factory)

        update = _make_callback_query(f"reject:{suggestion_id}")
        context = MagicMock()
        context.user_data = {}

        await handler.handle_callback(update, context)

        query = update.callback_query
        query.answer.assert_awaited_once()
        msg = query.edit_message_text.call_args[0][0]
        assert "거부 사유" in msg
        assert str(suggestion_id) == str(context.user_data["pending_rejection_id"])

    async def test_reject_not_found_sends_error(self, mock_db_session, suggestion_id):
        from backend.src.telegram.handlers.approval import ApprovalHandler

        result_suggestion = MagicMock()
        result_suggestion.scalar_one_or_none.return_value = None

        mock_db_session.execute = AsyncMock(return_value=result_suggestion)

        session_factory = _make_session_factory(mock_db_session)
        handler = ApprovalHandler(db_session_factory=session_factory)

        update = _make_callback_query(f"reject:{suggestion_id}")
        context = MagicMock()
        context.user_data = {}

        await handler.handle_callback(update, context)

        query = update.callback_query
        msg = query.edit_message_text.call_args[0][0]
        assert "찾을 수 없습니다" in msg

    async def test_reject_already_processed_sends_error(
        self, mock_db_session, suggestion_id, project_id
    ):
        from backend.src.telegram.handlers.approval import ApprovalHandler

        suggestion = _make_suggestion(suggestion_id, project_id)
        suggestion.status = SuggestionStatus.rejected

        result_suggestion = MagicMock()
        result_suggestion.scalar_one_or_none.return_value = suggestion

        mock_db_session.execute = AsyncMock(return_value=result_suggestion)

        session_factory = _make_session_factory(mock_db_session)
        handler = ApprovalHandler(db_session_factory=session_factory)

        update = _make_callback_query(f"reject:{suggestion_id}")
        context = MagicMock()
        context.user_data = {}

        await handler.handle_callback(update, context)

        query = update.callback_query
        msg = query.edit_message_text.call_args[0][0]
        assert "이미 처리" in msg


# ---------------------------------------------------------------------------
# Rejection reason text handler
# ---------------------------------------------------------------------------


class TestRejectionReasonHandler:
    """Test handling of rejection reason text message."""

    async def test_rejection_reason_updates_suggestion(
        self, mock_db_session, suggestion_id, project_id
    ):
        from backend.src.telegram.handlers.approval import ApprovalHandler

        suggestion = _make_suggestion(suggestion_id, project_id)

        result_suggestion = MagicMock()
        result_suggestion.scalar_one_or_none.return_value = suggestion

        mock_db_session.execute = AsyncMock(return_value=result_suggestion)

        session_factory = _make_session_factory(mock_db_session)
        handler = ApprovalHandler(db_session_factory=session_factory)

        update = _make_text_message("우선순위가 낮습니다")
        context = MagicMock()
        context.user_data = {"pending_rejection_id": str(suggestion_id)}

        await handler.handle_rejection_reason(update, context)

        assert suggestion.status == SuggestionStatus.rejected
        assert suggestion.rejection_reason == "우선순위가 낮습니다"
        mock_db_session.commit.assert_awaited_once()

    async def test_rejection_reason_sends_korean_confirmation(
        self, mock_db_session, suggestion_id, project_id
    ):
        from backend.src.telegram.handlers.approval import ApprovalHandler

        suggestion = _make_suggestion(suggestion_id, project_id)

        result_suggestion = MagicMock()
        result_suggestion.scalar_one_or_none.return_value = suggestion

        mock_db_session.execute = AsyncMock(return_value=result_suggestion)

        session_factory = _make_session_factory(mock_db_session)
        handler = ApprovalHandler(db_session_factory=session_factory)

        update = _make_text_message("우선순위가 낮습니다")
        context = MagicMock()
        context.user_data = {"pending_rejection_id": str(suggestion_id)}

        await handler.handle_rejection_reason(update, context)

        msg = update.message.reply_text.call_args[0][0]
        assert "거부" in msg
        assert suggestion.title in msg

    async def test_rejection_reason_clears_pending_state(
        self, mock_db_session, suggestion_id, project_id
    ):
        from backend.src.telegram.handlers.approval import ApprovalHandler

        suggestion = _make_suggestion(suggestion_id, project_id)

        result_suggestion = MagicMock()
        result_suggestion.scalar_one_or_none.return_value = suggestion

        mock_db_session.execute = AsyncMock(return_value=result_suggestion)

        session_factory = _make_session_factory(mock_db_session)
        handler = ApprovalHandler(db_session_factory=session_factory)

        update = _make_text_message("우선순위가 낮습니다")
        context = MagicMock()
        context.user_data = {"pending_rejection_id": str(suggestion_id)}

        await handler.handle_rejection_reason(update, context)

        assert "pending_rejection_id" not in context.user_data

    async def test_rejection_reason_no_pending_ignores(self, mock_db_session):
        from backend.src.telegram.handlers.approval import ApprovalHandler

        session_factory = _make_session_factory(mock_db_session)
        handler = ApprovalHandler(db_session_factory=session_factory)

        update = _make_text_message("some random text")
        context = MagicMock()
        context.user_data = {}

        result = await handler.handle_rejection_reason(update, context)

        assert result is False
        update.message.reply_text.assert_not_awaited()

    async def test_rejection_reason_suggestion_not_found(
        self, mock_db_session, suggestion_id
    ):
        from backend.src.telegram.handlers.approval import ApprovalHandler

        result_suggestion = MagicMock()
        result_suggestion.scalar_one_or_none.return_value = None

        mock_db_session.execute = AsyncMock(return_value=result_suggestion)

        session_factory = _make_session_factory(mock_db_session)
        handler = ApprovalHandler(db_session_factory=session_factory)

        update = _make_text_message("사유입니다")
        context = MagicMock()
        context.user_data = {"pending_rejection_id": str(suggestion_id)}

        await handler.handle_rejection_reason(update, context)

        msg = update.message.reply_text.call_args[0][0]
        assert "찾을 수 없습니다" in msg
        assert "pending_rejection_id" not in context.user_data


# ---------------------------------------------------------------------------
# Callback data parsing
# ---------------------------------------------------------------------------


class TestCallbackDataParsing:
    """Test parsing of callback data from inline keyboard."""

    async def test_invalid_callback_data_sends_error(self, mock_db_session):
        from backend.src.telegram.handlers.approval import ApprovalHandler

        session_factory = _make_session_factory(mock_db_session)
        handler = ApprovalHandler(db_session_factory=session_factory)

        update = _make_callback_query("invalid_data")
        context = MagicMock()

        await handler.handle_callback(update, context)

        query = update.callback_query
        query.answer.assert_awaited_once()

    async def test_invalid_uuid_sends_error(self, mock_db_session):
        from backend.src.telegram.handlers.approval import ApprovalHandler

        session_factory = _make_session_factory(mock_db_session)
        handler = ApprovalHandler(db_session_factory=session_factory)

        update = _make_callback_query("approve:not-a-uuid")
        context = MagicMock()

        await handler.handle_callback(update, context)

        query = update.callback_query
        query.answer.assert_awaited_once()
        msg = query.edit_message_text.call_args[0][0]
        assert "오류" in msg
