"""Tests for architect_service pure/utility functions."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from backend.src.core.architect_service import (
    build_message_history,
    clean_response,
    extract_design_context,
    slugify,
)
from backend.src.models import (
    DesignMessage,
    DesignSession,
    DesignSessionStatus,
    MessageRole,
    MessageType,
)


# ── slugify ───────────────────────────────────────────────────────────


def test_slugify_basic() -> None:
    assert slugify("My Phase Name") == "my-phase-name"


def test_slugify_special_chars() -> None:
    assert slugify("hello@world!") == "helloworld"


def test_slugify_multiple_hyphens() -> None:
    result = slugify("hello---world")
    assert result == "hello-world"


def test_slugify_strips_leading_trailing_hyphens() -> None:
    assert slugify("-hello-") == "hello"
    assert slugify("  --foo--  ") == "foo"


# ── clean_response ────────────────────────────────────────────────────


def test_clean_response_strips_finalize_marker() -> None:
    text = "Some text [FINALIZE] more text"
    cleaned, has_finalize, ctx = clean_response(text)

    assert has_finalize is True
    assert "[FINALIZE]" not in cleaned
    assert "Some text" in cleaned
    assert "more text" in cleaned


def test_clean_response_extracts_design_context() -> None:
    text = "Hello <design_context>project summary here</design_context> bye"
    cleaned, has_finalize, ctx = clean_response(text)

    assert has_finalize is False
    assert ctx == "project summary here"
    assert "<design_context>" not in cleaned
    assert "Hello" in cleaned
    assert "bye" in cleaned


def test_clean_response_no_markers() -> None:
    text = "Plain text with no markers"
    cleaned, has_finalize, ctx = clean_response(text)

    assert cleaned == text
    assert has_finalize is False
    assert ctx is None


# ── extract_design_context ────────────────────────────────────────────


def _make_message(
    role: MessageRole,
    content: str,
    msg_type: MessageType = MessageType.chat,
    created_at: datetime | None = None,
) -> MagicMock:
    msg = MagicMock(spec=DesignMessage)
    msg.role = role
    msg.content = content
    msg.message_type = msg_type
    msg.created_at = created_at or datetime.now(timezone.utc)
    return msg


def _make_session(messages: list) -> MagicMock:
    session = MagicMock(spec=DesignSession)
    session.messages = messages
    session.status = DesignSessionStatus.active
    return session


def test_extract_design_context_from_messages() -> None:
    """Finds <design_context> in the last assistant chat message."""
    msgs = [
        _make_message(MessageRole.user, "hi", created_at=datetime(2025, 1, 1, tzinfo=timezone.utc)),
        _make_message(
            MessageRole.assistant,
            "Here is context <design_context>important stuff</design_context>",
            created_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
        ),
        _make_message(
            MessageRole.assistant,
            "Later message without context",
            created_at=datetime(2025, 1, 3, tzinfo=timezone.utc),
        ),
    ]
    session = _make_session(msgs)

    # The function takes the LAST assistant chat message sorted by created_at.
    # The last one has no context tag, so returns None.
    result = extract_design_context(session)
    assert result is None

    # Now make the latest one have context:
    msgs[2].content = "Final <design_context>final context</design_context>"
    result = extract_design_context(session)
    assert result == "final context"


def test_extract_design_context_no_context() -> None:
    """Returns None when no assistant messages contain <design_context>."""
    msgs = [
        _make_message(MessageRole.user, "hi"),
        _make_message(MessageRole.assistant, "hello, how can I help?"),
    ]
    session = _make_session(msgs)

    assert extract_design_context(session) is None


def test_extract_design_context_no_assistant_messages() -> None:
    """Returns None when session has no assistant chat messages."""
    msgs = [_make_message(MessageRole.user, "hi")]
    session = _make_session(msgs)

    assert extract_design_context(session) is None


# ── build_message_history ─────────────────────────────────────────────


@patch("backend.src.core.architect_service.get_prompt", return_value="system prompt")
def test_build_message_history_sorts_by_timestamp(mock_get_prompt: MagicMock) -> None:
    """Messages are ordered chronologically in the history."""
    msg_early = _make_message(
        MessageRole.user, "first", created_at=datetime(2025, 1, 1, tzinfo=timezone.utc)
    )
    msg_late = _make_message(
        MessageRole.assistant, "second", created_at=datetime(2025, 1, 2, tzinfo=timezone.utc)
    )
    msg_middle = _make_message(
        MessageRole.user, "middle", created_at=datetime(2025, 1, 1, 12, tzinfo=timezone.utc)
    )

    session = _make_session([msg_late, msg_early, msg_middle])

    history = build_message_history(session)

    assert history[0]["role"] == "system"
    assert history[0]["content"] == "system prompt"
    # Chat messages should be chronologically sorted
    assert history[1]["content"] == "first"
    assert history[2]["content"] == "middle"
    assert history[3]["content"] == "second"

    mock_get_prompt.assert_called_once_with("architect", "system")


@patch("backend.src.core.architect_service.get_prompt", return_value="system prompt")
def test_build_message_history_filters_chat_only(mock_get_prompt: MagicMock) -> None:
    """Only chat-type messages are included; internal messages are excluded."""
    chat_msg = _make_message(MessageRole.user, "chat message", msg_type=MessageType.chat)
    internal_msg = _make_message(MessageRole.assistant, "internal", msg_type=MessageType.internal)

    session = _make_session([chat_msg, internal_msg])

    history = build_message_history(session)

    # system + 1 chat message (internal excluded)
    assert len(history) == 2
    assert history[1]["content"] == "chat message"
