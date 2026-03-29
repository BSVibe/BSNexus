"""Tests for architect_service pure/utility functions and service methods."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.src.core.architect_service import (
    ArchitectService,
    build_llm_config,
    build_message_history,
    build_project_context,
    clean_response,
    extract_design_context,
    slugify,
    strip_action_markers,
    strip_design_context,
)
from backend.src.core.llm_client import LLMConfig, LLMError
from backend.src.models import (
    DesignMessage,
    DesignSession,
    DesignSessionStatus,
    MessageRole,
    MessageType,
    Phase,
    PhaseStatus,
    Project,
    ProjectStatus,
    Task,
    TaskPriority,
    TaskStatus,
    TaskType,
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
    msg_early = _make_message(MessageRole.user, "first", created_at=datetime(2025, 1, 1, tzinfo=timezone.utc))
    msg_late = _make_message(MessageRole.assistant, "second", created_at=datetime(2025, 1, 2, tzinfo=timezone.utc))
    msg_middle = _make_message(MessageRole.user, "middle", created_at=datetime(2025, 1, 1, 12, tzinfo=timezone.utc))

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


# ── send_message error handling ──────────────────────────────────────


@pytest.mark.asyncio
@patch("backend.src.core.architect_service.build_llm_config")
@patch("backend.src.core.architect_service.LLMClient")
async def test_send_message_raises_value_error_on_llm_failure(
    mock_llm_cls: MagicMock,
    mock_build_config: MagicMock,
    db_session,
) -> None:
    """send_message propagates LLMError from LLMClient."""
    now = datetime.now(timezone.utc)
    session = DesignSession(
        id=uuid.uuid4(),
        status=DesignSessionStatus.active,
        llm_config={"api_key": "test-key", "model": "test-model"},
        created_at=now,
        updated_at=now,
    )
    db_session.add(session)
    await db_session.flush()

    mock_build_config.return_value = MagicMock()
    mock_client = AsyncMock()
    mock_client.chat = AsyncMock(side_effect=LLMError("connection timeout"))
    mock_llm_cls.return_value = mock_client

    service = ArchitectService(db_session)
    with pytest.raises(LLMError, match="connection timeout"):
        await service.send_message(session.id, "Hello")


# ── strip_action_markers ─────────────────────────────────────────────


def test_strip_action_markers_removes_create_task() -> None:
    text = 'Hello [CREATE_TASK]{"title": "t1"}[/CREATE_TASK] world'
    result = strip_action_markers(text)
    assert "[CREATE_TASK]" not in result
    assert "Hello" in result
    assert "world" in result


def test_strip_action_markers_removes_modify_task() -> None:
    text = 'Start [MODIFY_TASK]{"task_id": "abc"}[/MODIFY_TASK] end'
    result = strip_action_markers(text)
    assert "[MODIFY_TASK]" not in result
    assert "Start" in result


# ── strip_design_context ─────────────────────────────────────────────


def test_strip_design_context_removes_tag() -> None:
    text = "Before <design_context>ctx data</design_context> after"
    result = strip_design_context(text)
    assert "<design_context>" not in result
    assert "Before" in result
    assert "after" in result


def test_strip_design_context_no_tag() -> None:
    text = "Plain text"
    assert strip_design_context(text) == text


# ── build_llm_config ─────────────────────────────────────────────────


def test_build_llm_config_with_api_key() -> None:
    config = build_llm_config({"api_key": "sk-test", "model": "gpt-4"})
    assert isinstance(config, LLMConfig)
    assert config.api_key == "sk-test"
    assert config.model == "gpt-4"


def test_build_llm_config_missing_api_key_raises() -> None:
    with pytest.raises(ValueError, match="api_key is required"):
        build_llm_config({"model": "gpt-4"})


def test_build_llm_config_none_raises() -> None:
    with pytest.raises(ValueError, match="api_key is required"):
        build_llm_config(None)


def test_build_llm_config_empty_dict_raises() -> None:
    with pytest.raises(ValueError, match="api_key is required"):
        build_llm_config({})


def test_build_llm_config_empty_model_skipped() -> None:
    """Empty string model should not be passed to LLMConfig."""
    config = build_llm_config({"api_key": "sk-test", "model": ""})
    assert config.api_key == "sk-test"
    # model defaults to settings.default_llm_model, not empty string
    assert config.model != ""


def test_build_llm_config_with_base_url() -> None:
    config = build_llm_config({"api_key": "sk-test", "base_url": "http://localhost:8080"})
    assert config.base_url == "http://localhost:8080"


def test_build_llm_config_empty_base_url_skipped() -> None:
    config = build_llm_config({"api_key": "sk-test", "base_url": ""})
    assert config.base_url is None


# ── build_project_context ────────────────────────────────────────────


def test_build_project_context_formats_project() -> None:
    project = MagicMock(spec=Project)
    project.name = "TestProj"
    project.status = ProjectStatus.active
    project.description = "A test project"

    phase = MagicMock(spec=Phase)
    phase.name = "Phase 1"
    phase.order = 1
    phase.status = PhaseStatus.active

    task = MagicMock(spec=Task)
    task.title = "Task A"
    task.status = TaskStatus.ready
    task.task_type = TaskType.feature
    task.id = uuid.uuid4()
    task.created_at = datetime(2025, 1, 1, tzinfo=timezone.utc)

    task_bug = MagicMock(spec=Task)
    task_bug.title = "Fix Bug"
    task_bug.status = TaskStatus.waiting
    task_bug.task_type = TaskType.bug
    task_bug.id = uuid.uuid4()
    task_bug.created_at = datetime(2025, 1, 2, tzinfo=timezone.utc)

    phase.tasks = [task, task_bug]
    project.phases = [phase]

    result = build_project_context(project)
    assert "TestProj" in result
    assert "active" in result
    assert "Phase 1" in result
    assert "Task A" in result
    assert "(bug)" in result  # non-feature task type shown


# ── build_message_history (project_bound path) ───────────────────────


@patch("backend.src.core.architect_service.get_prompt")
@patch("backend.src.core.architect_service.build_project_context", return_value="project context text")
def test_build_message_history_project_bound(mock_build_ctx: MagicMock, mock_get_prompt: MagicMock) -> None:
    """Project-bound session uses system_project_bound prompt."""
    mock_get_prompt.side_effect = lambda *args: (
        "bound prompt: {project_context}" if args == ("architect", "system_project_bound") else "system prompt"
    )

    session = _make_session([])
    session.status = DesignSessionStatus.project_bound

    project = MagicMock(spec=Project)
    history = build_message_history(session, project=project)

    assert history[0]["role"] == "system"
    assert "project context text" in history[0]["content"]
    mock_build_ctx.assert_called_once_with(project)


# ── ArchitectService.create_session ──────────────────────────────────


async def test_create_session(db_session) -> None:
    service = ArchitectService(db_session)
    session = await service.create_session(
        llm_config_dict={"api_key": "test-key", "model": "gpt-4"},
        name="Test Session",
    )
    assert session is not None
    assert session.name == "Test Session"
    assert session.llm_config["api_key"] == "test-key"
    assert session.status == DesignSessionStatus.active


async def test_create_session_without_name(db_session) -> None:
    service = ArchitectService(db_session)
    session = await service.create_session(llm_config_dict={"api_key": "k"})
    assert session is not None
    assert session.name is None


# ── ArchitectService.send_message (happy path) ──────────────────────


@patch("backend.src.core.architect_service.build_llm_config")
@patch("backend.src.core.architect_service.LLMClient")
async def test_send_message_happy_path(mock_llm_cls: MagicMock, mock_build_config: MagicMock, db_session) -> None:
    now = datetime.now(timezone.utc)
    session = DesignSession(
        id=uuid.uuid4(),
        status=DesignSessionStatus.active,
        llm_config={"api_key": "test-key", "model": "test"},
        created_at=now,
        updated_at=now,
    )
    db_session.add(session)
    await db_session.flush()

    mock_build_config.return_value = MagicMock()
    mock_client = AsyncMock()
    mock_client.chat = AsyncMock(return_value="Here is my response")
    mock_llm_cls.return_value = mock_client

    service = ArchitectService(db_session)
    assistant_msg, cleaned, has_finalize, design_ctx = await service.send_message(session.id, "Hello")

    assert assistant_msg is not None
    assert cleaned == "Here is my response"
    assert has_finalize is False
    assert design_ctx is None
    mock_client.chat.assert_called_once()


@patch("backend.src.core.architect_service.build_llm_config")
@patch("backend.src.core.architect_service.LLMClient")
async def test_send_message_with_finalize_marker(
    mock_llm_cls: MagicMock, mock_build_config: MagicMock, db_session
) -> None:
    now = datetime.now(timezone.utc)
    session = DesignSession(
        id=uuid.uuid4(),
        status=DesignSessionStatus.active,
        llm_config={"api_key": "test-key"},
        created_at=now,
        updated_at=now,
    )
    db_session.add(session)
    await db_session.flush()

    mock_build_config.return_value = MagicMock()
    mock_client = AsyncMock()
    mock_client.chat = AsyncMock(return_value="Done [FINALIZE] <design_context>ctx</design_context>")
    mock_llm_cls.return_value = mock_client

    service = ArchitectService(db_session)
    _, cleaned, has_finalize, design_ctx = await service.send_message(session.id, "Finalize")

    assert has_finalize is True
    assert design_ctx == "ctx"
    assert "[FINALIZE]" not in cleaned


async def test_send_message_session_not_found(db_session) -> None:
    service = ArchitectService(db_session)
    with pytest.raises(ValueError, match="Session not found"):
        await service.send_message(uuid.uuid4(), "Hello")


async def test_send_message_cancelled_session(db_session) -> None:
    now = datetime.now(timezone.utc)
    session = DesignSession(
        id=uuid.uuid4(),
        status=DesignSessionStatus.cancelled,
        llm_config={"api_key": "k"},
        created_at=now,
        updated_at=now,
    )
    db_session.add(session)
    await db_session.flush()

    service = ArchitectService(db_session)
    with pytest.raises(ValueError, match="Session is cancelled"):
        await service.send_message(session.id, "Hello")


# ── ArchitectService.send_message (project_bound with action markers) ─


@patch("backend.src.core.architect_service.build_llm_config")
@patch("backend.src.core.architect_service.LLMClient")
async def test_send_message_project_bound_strips_markers(
    mock_llm_cls: MagicMock, mock_build_config: MagicMock, db_session
) -> None:
    """Project-bound session executes action markers and strips them."""
    now = datetime.now(timezone.utc)
    project = Project(name="P", description="d", repo_path="/tmp", status=ProjectStatus.active)
    db_session.add(project)
    await db_session.flush()

    session = DesignSession(
        id=uuid.uuid4(),
        status=DesignSessionStatus.project_bound,
        project_id=project.id,
        llm_config={"api_key": "k"},
        created_at=now,
        updated_at=now,
    )
    db_session.add(session)
    await db_session.flush()

    mock_build_config.return_value = MagicMock()
    mock_client = AsyncMock()
    # Response with an action marker that should be stripped
    mock_client.chat = AsyncMock(return_value='Response text [CREATE_TASK]{"title":"T"}[/CREATE_TASK] end')
    mock_llm_cls.return_value = mock_client

    service = ArchitectService(db_session)

    mock_project = MagicMock(spec=Project)
    mock_project.phases = []

    with patch.object(service, "execute_action_markers", new_callable=AsyncMock, return_value=[]):
        with patch.object(service, "load_project_with_tasks", new_callable=AsyncMock, return_value=mock_project):
            with patch("backend.src.core.architect_service.build_project_context", return_value="ctx"):
                _, cleaned, _, _ = await service.send_message(session.id, "Create task")

    assert "[CREATE_TASK]" not in cleaned
    assert "Response text" in cleaned


# ── ArchitectService.list_sessions / get_session ─────────────────────


async def test_list_sessions(db_session) -> None:
    now = datetime.now(timezone.utc)
    s1 = DesignSession(
        id=uuid.uuid4(),
        status=DesignSessionStatus.active,
        llm_config={"api_key": "k"},
        created_at=now,
        updated_at=now,
    )
    s2 = DesignSession(
        id=uuid.uuid4(),
        status=DesignSessionStatus.cancelled,
        llm_config={"api_key": "k"},
        created_at=now,
        updated_at=now,
    )
    db_session.add_all([s1, s2])
    await db_session.flush()
    await db_session.commit()

    service = ArchitectService(db_session)
    all_sessions = await service.list_sessions()
    assert len(all_sessions) >= 2

    active_sessions = await service.list_sessions(status=DesignSessionStatus.active)
    assert all(s.status == DesignSessionStatus.active for s in active_sessions)


async def test_get_session(db_session) -> None:
    now = datetime.now(timezone.utc)
    s = DesignSession(
        id=uuid.uuid4(),
        status=DesignSessionStatus.active,
        llm_config={"api_key": "k"},
        created_at=now,
        updated_at=now,
    )
    db_session.add(s)
    await db_session.flush()
    await db_session.commit()

    service = ArchitectService(db_session)
    loaded = await service.get_session(s.id)
    assert loaded is not None
    assert loaded.id == s.id


async def test_get_session_not_found(db_session) -> None:
    service = ArchitectService(db_session)
    result = await service.get_session(uuid.uuid4())
    assert result is None


# ── ArchitectService.load_project_with_tasks ─────────────────────────


async def test_load_project_with_tasks(db_session) -> None:
    project = Project(name="P", description="d", repo_path="/tmp", status=ProjectStatus.active)
    db_session.add(project)
    await db_session.flush()

    phase = Phase(project_id=project.id, name="Ph1", branch_name="b", order=1)
    db_session.add(phase)
    await db_session.flush()

    task = Task(
        project_id=project.id,
        phase_id=phase.id,
        title="T1",
        branch_name="b",
        status=TaskStatus.ready,
        priority=TaskPriority.medium,
    )
    db_session.add(task)
    await db_session.commit()

    service = ArchitectService(db_session)
    loaded = await service.load_project_with_tasks(project.id)
    assert loaded is not None
    assert len(loaded.phases) == 1
    assert len(loaded.phases[0].tasks) == 1


async def test_load_project_with_tasks_not_found(db_session) -> None:
    service = ArchitectService(db_session)
    result = await service.load_project_with_tasks(uuid.uuid4())
    assert result is None


# ── ArchitectService.finalize ────────────────────────────────────────


async def test_finalize_no_session_factory_raises(db_session) -> None:
    service = ArchitectService(db_session, session_factory=None)
    with pytest.raises(RuntimeError, match="session_factory is required"):
        await service.finalize(uuid.uuid4(), "/tmp/repo")


@patch("backend.src.core.architect_service.get_prompt", return_value="prompt {design_context}")
@patch("backend.src.core.architect_service.LLMClient")
async def test_finalize_happy_path(
    mock_llm_cls: MagicMock, mock_get_prompt: MagicMock, db_session, test_session_maker
) -> None:
    """Finalize creates project, phases, and tasks from LLM JSON response."""
    now = datetime.now(timezone.utc)
    session = DesignSession(
        id=uuid.uuid4(),
        status=DesignSessionStatus.active,
        llm_config={"api_key": "test-key", "model": "gpt-4"},
        created_at=now,
        updated_at=now,
    )
    db_session.add(session)
    await db_session.flush()
    await db_session.commit()

    llm_response = {
        "project_name": "FinProj",
        "project_description": "A finalized project",
        "phases": [
            {
                "name": "Setup",
                "description": "Initial setup",
                "tasks": [
                    {
                        "title": "Task 1",
                        "description": "First task",
                        "priority": "high",
                        "worker_prompt": "Do task 1",
                        "qa_prompt": "Check task 1",
                    },
                    {
                        "title": "Task 2",
                        "description": "Second task",
                        "priority": "low",
                        "worker_prompt": "Do task 2",
                        "qa_prompt": "Check task 2",
                        "depends_on_indices": [0],
                    },
                ],
            },
            {
                "name": "Build",
                "description": "Build phase",
                "tasks": [
                    {
                        "title": "Task 3",
                        "description": "Build task",
                        "priority": "medium",
                        "worker_prompt": "Build it",
                        "qa_prompt": "Verify build",
                    },
                ],
            },
        ],
    }

    mock_client = AsyncMock()
    mock_client.structured_output = AsyncMock(return_value=llm_response)
    mock_llm_cls.return_value = mock_client

    service = ArchitectService(db_session, session_factory=test_session_maker)
    project = await service.finalize(session.id, "/tmp/repo")

    assert project is not None
    assert project.name == "FinProj"
    assert project.description == "A finalized project"
    assert project.status == ProjectStatus.design
    assert len(project.phases) == 2

    # First phase should be active
    first_phase = sorted(project.phases, key=lambda p: p.order)[0]
    assert first_phase.status == PhaseStatus.active
    assert first_phase.name == "Setup"

    # Second phase should be pending
    second_phase = sorted(project.phases, key=lambda p: p.order)[1]
    assert second_phase.status == PhaseStatus.pending


@patch("backend.src.core.architect_service.get_prompt", return_value="prompt {design_context}")
@patch("backend.src.core.architect_service.LLMClient")
async def test_finalize_with_pm_config(
    mock_llm_cls: MagicMock, mock_get_prompt: MagicMock, db_session, test_session_maker
) -> None:
    """pm_llm_config is stored in project.llm_config['pm']."""
    now = datetime.now(timezone.utc)
    session = DesignSession(
        id=uuid.uuid4(),
        status=DesignSessionStatus.active,
        llm_config={"api_key": "k"},
        created_at=now,
        updated_at=now,
    )
    db_session.add(session)
    await db_session.flush()
    await db_session.commit()

    mock_client = AsyncMock()
    mock_client.structured_output = AsyncMock(
        return_value={
            "project_name": "P",
            "project_description": "d",
            "phases": [{"name": "Ph", "tasks": [{"title": "T"}]}],
        }
    )
    mock_llm_cls.return_value = mock_client

    service = ArchitectService(db_session, session_factory=test_session_maker)
    pm_config = {"api_key": "pm-key", "model": "pm-model"}
    project = await service.finalize(session.id, "/tmp/r", pm_llm_config=pm_config)

    assert project.llm_config["pm"] == pm_config
    assert project.llm_config["architect"]["api_key"] == "k"


@patch("backend.src.core.architect_service.get_prompt", return_value="prompt {design_context}")
@patch("backend.src.core.architect_service.LLMClient")
async def test_finalize_with_design_context(
    mock_llm_cls: MagicMock, mock_get_prompt: MagicMock, db_session, test_session_maker
) -> None:
    """When design_context is available, finalize uses it directly."""
    now = datetime.now(timezone.utc)
    session = DesignSession(
        id=uuid.uuid4(),
        status=DesignSessionStatus.active,
        llm_config={"api_key": "k"},
        created_at=now,
        updated_at=now,
    )
    db_session.add(session)
    await db_session.flush()

    # Add an assistant message with design_context
    from backend.src.models import DesignMessage as DM

    msg = DM(
        session_id=session.id,
        role=MessageRole.assistant,
        content="Response <design_context>important design info</design_context>",
        message_type=MessageType.chat,
    )
    db_session.add(msg)
    await db_session.flush()
    await db_session.commit()

    mock_client = AsyncMock()
    mock_client.structured_output = AsyncMock(
        return_value={
            "project_name": "P",
            "phases": [{"name": "Ph", "tasks": [{"title": "T"}]}],
        }
    )
    mock_llm_cls.return_value = mock_client

    service = ArchitectService(db_session, session_factory=test_session_maker)
    project = await service.finalize(session.id, "/tmp/r")
    assert project is not None

    # Verify structured_output was called with messages containing the design context
    call_args = mock_client.structured_output.call_args
    messages = call_args.kwargs.get("messages", call_args.args[0] if call_args.args else None)
    # When design_context is found, messages should be shorter (system + finalize only)
    assert len(messages) == 2  # system prompt + finalize prompt


async def test_finalize_session_not_found(db_session, test_session_maker) -> None:
    service = ArchitectService(db_session, session_factory=test_session_maker)
    with pytest.raises(ValueError, match="Session not found"):
        await service.finalize(uuid.uuid4(), "/tmp/repo")


async def test_finalize_session_not_active(db_session, test_session_maker) -> None:
    now = datetime.now(timezone.utc)
    session = DesignSession(
        id=uuid.uuid4(),
        status=DesignSessionStatus.cancelled,
        llm_config={"api_key": "k"},
        created_at=now,
        updated_at=now,
    )
    db_session.add(session)
    await db_session.flush()
    await db_session.commit()

    service = ArchitectService(db_session, session_factory=test_session_maker)
    with pytest.raises(ValueError, match="Session is not active"):
        await service.finalize(session.id, "/tmp/repo")


@patch("backend.src.core.architect_service.get_prompt", return_value="prompt {design_context}")
@patch("backend.src.core.architect_service.LLMClient")
async def test_finalize_no_phases_raises(
    mock_llm_cls: MagicMock, mock_get_prompt: MagicMock, db_session, test_session_maker
) -> None:
    """Finalize raises ValueError when LLM returns empty phases."""
    now = datetime.now(timezone.utc)
    session = DesignSession(
        id=uuid.uuid4(),
        status=DesignSessionStatus.active,
        llm_config={"api_key": "k"},
        created_at=now,
        updated_at=now,
    )
    db_session.add(session)
    await db_session.flush()
    await db_session.commit()

    mock_client = AsyncMock()
    mock_client.structured_output = AsyncMock(
        return_value={
            "project_name": "P",
            "phases": [],
        }
    )
    mock_llm_cls.return_value = mock_client

    service = ArchitectService(db_session, session_factory=test_session_maker)
    with pytest.raises(ValueError, match="at least one phase"):
        await service.finalize(session.id, "/tmp/repo")


@patch("backend.src.core.architect_service.get_prompt", return_value="prompt {design_context}")
@patch("backend.src.core.architect_service.LLMClient")
async def test_finalize_invalid_priority_falls_back(
    mock_llm_cls: MagicMock, mock_get_prompt: MagicMock, db_session, test_session_maker
) -> None:
    """Invalid priority string falls back to medium."""
    now = datetime.now(timezone.utc)
    session = DesignSession(
        id=uuid.uuid4(),
        status=DesignSessionStatus.active,
        llm_config={"api_key": "k"},
        created_at=now,
        updated_at=now,
    )
    db_session.add(session)
    await db_session.flush()
    await db_session.commit()

    mock_client = AsyncMock()
    mock_client.structured_output = AsyncMock(
        return_value={
            "project_name": "P",
            "phases": [{"name": "Ph", "tasks": [{"title": "T", "priority": "INVALID"}]}],
        }
    )
    mock_llm_cls.return_value = mock_client

    service = ArchitectService(db_session, session_factory=test_session_maker)
    project = await service.finalize(session.id, "/tmp/repo")
    assert project is not None  # Should succeed despite invalid priority


@patch("backend.src.core.architect_service.get_prompt", return_value="prompt {design_context}")
@patch("backend.src.core.architect_service.LLMClient")
async def test_finalize_already_finalized_returns_existing(
    mock_llm_cls: MagicMock, mock_get_prompt: MagicMock, db_session, test_session_maker
) -> None:
    """Finalize on an already-finalized session returns the existing project."""
    now = datetime.now(timezone.utc)
    project = Project(name="Existing", description="d", repo_path="/tmp", status=ProjectStatus.active)
    db_session.add(project)
    await db_session.flush()

    session = DesignSession(
        id=uuid.uuid4(),
        status=DesignSessionStatus.project_bound,
        project_id=project.id,
        llm_config={"api_key": "k"},
        created_at=now,
        updated_at=now,
    )
    db_session.add(session)
    await db_session.flush()
    await db_session.commit()

    service = ArchitectService(db_session, session_factory=test_session_maker)
    result = await service.finalize(session.id, "/tmp/repo")
    assert result.id == project.id
    # LLM should NOT have been called
    mock_llm_cls.assert_not_called()


# ── ArchitectService.execute_action_markers ──────────────────────────


async def test_execute_action_markers_create_task(db_session) -> None:
    """CREATE_TASK markers create new tasks in the active phase."""
    project = Project(name="P", description="d", repo_path="/tmp", status=ProjectStatus.active)
    db_session.add(project)
    await db_session.flush()

    phase = Phase(
        project_id=project.id,
        name="Active Phase",
        branch_name="phase/active",
        order=1,
        status=PhaseStatus.active,
    )
    db_session.add(phase)
    await db_session.flush()
    await db_session.commit()

    session_mock = MagicMock(spec=DesignSession)
    session_mock.project_id = project.id

    task_json = json.dumps(
        {
            "title": "New Task",
            "description": "Created via marker",
            "priority": "high",
            "task_type": "bug",
            "worker_prompt": "fix it",
            "qa_prompt": "verify fix",
        }
    )
    text = f"Some text [CREATE_TASK]{task_json}[/CREATE_TASK] more text"

    service = ArchitectService(db_session)
    actions = await service.execute_action_markers(text, session_mock)

    assert len(actions) == 1
    assert actions[0]["type"] == "task_created"
    assert actions[0]["title"] == "New Task"


async def test_execute_action_markers_no_project_id(db_session) -> None:
    """Returns empty list when session has no project_id."""
    session_mock = MagicMock(spec=DesignSession)
    session_mock.project_id = None

    service = ArchitectService(db_session)
    actions = await service.execute_action_markers("[CREATE_TASK]{}[/CREATE_TASK]", session_mock)
    assert actions == []


async def test_execute_action_markers_no_active_phase(db_session) -> None:
    """CREATE_TASK is skipped when no active phase exists."""
    project = Project(name="P", description="d", repo_path="/tmp", status=ProjectStatus.active)
    db_session.add(project)
    await db_session.flush()

    # Only a pending phase, not active
    phase = Phase(
        project_id=project.id,
        name="Pending",
        branch_name="b",
        order=1,
        status=PhaseStatus.pending,
    )
    db_session.add(phase)
    await db_session.flush()
    await db_session.commit()

    session_mock = MagicMock(spec=DesignSession)
    session_mock.project_id = project.id

    text = '[CREATE_TASK]{"title": "T"}[/CREATE_TASK]'
    service = ArchitectService(db_session)
    actions = await service.execute_action_markers(text, session_mock)
    assert actions == []


async def test_execute_action_markers_invalid_json(db_session) -> None:
    """Invalid JSON in CREATE_TASK is silently skipped."""
    project = Project(name="P", description="d", repo_path="/tmp", status=ProjectStatus.active)
    db_session.add(project)
    await db_session.flush()

    phase = Phase(
        project_id=project.id,
        name="Ph",
        branch_name="b",
        order=1,
        status=PhaseStatus.active,
    )
    db_session.add(phase)
    await db_session.flush()
    await db_session.commit()

    session_mock = MagicMock(spec=DesignSession)
    session_mock.project_id = project.id

    text = "[CREATE_TASK]not valid json[/CREATE_TASK]"
    service = ArchitectService(db_session)
    actions = await service.execute_action_markers(text, session_mock)
    assert actions == []


async def test_execute_action_markers_modify_task(db_session) -> None:
    """MODIFY_TASK markers update existing tasks."""
    project = Project(name="P", description="d", repo_path="/tmp", status=ProjectStatus.active)
    db_session.add(project)
    await db_session.flush()

    phase = Phase(
        project_id=project.id,
        name="Ph",
        branch_name="b",
        order=1,
        status=PhaseStatus.active,
    )
    db_session.add(phase)
    await db_session.flush()

    task = Task(
        project_id=project.id,
        phase_id=phase.id,
        title="Original",
        branch_name="b",
        status=TaskStatus.ready,
        priority=TaskPriority.medium,
    )
    db_session.add(task)
    await db_session.flush()
    await db_session.commit()

    session_mock = MagicMock(spec=DesignSession)
    session_mock.project_id = project.id

    modify_json = json.dumps(
        {
            "task_id": str(task.id),
            "title": "Modified Title",
            "description": "New desc",
            "priority": "high",
            "worker_prompt": "new prompt",
            "qa_prompt": "new qa",
        }
    )
    text = f"[MODIFY_TASK]{modify_json}[/MODIFY_TASK]"

    service = ArchitectService(db_session)
    actions = await service.execute_action_markers(text, session_mock)

    assert len(actions) == 1
    assert actions[0]["type"] == "task_modified"
    assert actions[0]["title"] == "Modified Title"


async def test_execute_action_markers_modify_invalid_task_id(db_session) -> None:
    """MODIFY_TASK with invalid UUID is skipped."""
    project = Project(name="P", description="d", repo_path="/tmp", status=ProjectStatus.active)
    db_session.add(project)
    await db_session.flush()
    await db_session.commit()

    session_mock = MagicMock(spec=DesignSession)
    session_mock.project_id = project.id

    modify_json = json.dumps({"task_id": "not-a-uuid", "title": "X"})
    text = f"[MODIFY_TASK]{modify_json}[/MODIFY_TASK]"

    service = ArchitectService(db_session)
    actions = await service.execute_action_markers(text, session_mock)
    assert actions == []


async def test_execute_action_markers_modify_no_task_id(db_session) -> None:
    """MODIFY_TASK without task_id key is skipped."""
    project = Project(name="P", description="d", repo_path="/tmp", status=ProjectStatus.active)
    db_session.add(project)
    await db_session.flush()
    await db_session.commit()

    session_mock = MagicMock(spec=DesignSession)
    session_mock.project_id = project.id

    text = '[MODIFY_TASK]{"title": "X"}[/MODIFY_TASK]'
    service = ArchitectService(db_session)
    actions = await service.execute_action_markers(text, session_mock)
    assert actions == []


async def test_execute_action_markers_modify_task_wrong_status(db_session) -> None:
    """MODIFY_TASK is skipped for tasks not in waiting/ready status."""
    project = Project(name="P", description="d", repo_path="/tmp", status=ProjectStatus.active)
    db_session.add(project)
    await db_session.flush()

    phase = Phase(
        project_id=project.id,
        name="Ph",
        branch_name="b",
        order=1,
        status=PhaseStatus.active,
    )
    db_session.add(phase)
    await db_session.flush()

    task = Task(
        project_id=project.id,
        phase_id=phase.id,
        title="InProgress",
        branch_name="b",
        status=TaskStatus.in_progress,
        priority=TaskPriority.medium,
    )
    db_session.add(task)
    await db_session.flush()
    await db_session.commit()

    session_mock = MagicMock(spec=DesignSession)
    session_mock.project_id = project.id

    modify_json = json.dumps({"task_id": str(task.id), "title": "Changed"})
    text = f"[MODIFY_TASK]{modify_json}[/MODIFY_TASK]"

    service = ArchitectService(db_session)
    actions = await service.execute_action_markers(text, session_mock)
    assert actions == []


async def test_execute_action_markers_modify_task_wrong_project(db_session) -> None:
    """MODIFY_TASK is skipped for tasks belonging to a different project."""
    project1 = Project(name="P1", description="d", repo_path="/tmp", status=ProjectStatus.active)
    project2 = Project(name="P2", description="d", repo_path="/tmp", status=ProjectStatus.active)
    db_session.add_all([project1, project2])
    await db_session.flush()

    phase = Phase(project_id=project1.id, name="Ph", branch_name="b", order=1, status=PhaseStatus.active)
    db_session.add(phase)
    await db_session.flush()

    task = Task(
        project_id=project1.id,
        phase_id=phase.id,
        title="T",
        branch_name="b",
        status=TaskStatus.ready,
        priority=TaskPriority.medium,
    )
    db_session.add(task)
    await db_session.flush()
    await db_session.commit()

    session_mock = MagicMock(spec=DesignSession)
    session_mock.project_id = project2.id  # Different project

    modify_json = json.dumps({"task_id": str(task.id), "title": "Hacked"})
    text = f"[MODIFY_TASK]{modify_json}[/MODIFY_TASK]"

    service = ArchitectService(db_session)
    actions = await service.execute_action_markers(text, session_mock)
    assert actions == []


async def test_execute_action_markers_modify_invalid_json(db_session) -> None:
    """Invalid JSON in MODIFY_TASK is silently skipped."""
    project = Project(name="P", description="d", repo_path="/tmp", status=ProjectStatus.active)
    db_session.add(project)
    await db_session.flush()
    await db_session.commit()

    session_mock = MagicMock(spec=DesignSession)
    session_mock.project_id = project.id

    text = "[MODIFY_TASK]not valid json[/MODIFY_TASK]"
    service = ArchitectService(db_session)
    actions = await service.execute_action_markers(text, session_mock)
    assert actions == []


async def test_execute_action_markers_modify_invalid_priority_ignored(db_session) -> None:
    """Invalid priority in MODIFY_TASK is silently ignored (priority unchanged)."""
    project = Project(name="P", description="d", repo_path="/tmp", status=ProjectStatus.active)
    db_session.add(project)
    await db_session.flush()

    phase = Phase(
        project_id=project.id,
        name="Ph",
        branch_name="b",
        order=1,
        status=PhaseStatus.active,
    )
    db_session.add(phase)
    await db_session.flush()

    task = Task(
        project_id=project.id,
        phase_id=phase.id,
        title="T",
        branch_name="b",
        status=TaskStatus.ready,
        priority=TaskPriority.medium,
    )
    db_session.add(task)
    await db_session.flush()
    await db_session.commit()

    session_mock = MagicMock(spec=DesignSession)
    session_mock.project_id = project.id

    modify_json = json.dumps({"task_id": str(task.id), "priority": "BOGUS"})
    text = f"[MODIFY_TASK]{modify_json}[/MODIFY_TASK]"

    service = ArchitectService(db_session)
    actions = await service.execute_action_markers(text, session_mock)
    assert len(actions) == 1
    # Priority should remain medium
    assert task.priority == TaskPriority.medium


async def test_execute_action_markers_create_invalid_task_type(db_session) -> None:
    """Invalid task_type in CREATE_TASK falls back to feature."""
    project = Project(name="P", description="d", repo_path="/tmp", status=ProjectStatus.active)
    db_session.add(project)
    await db_session.flush()

    phase = Phase(
        project_id=project.id,
        name="Ph",
        branch_name="b",
        order=1,
        status=PhaseStatus.active,
    )
    db_session.add(phase)
    await db_session.flush()
    await db_session.commit()

    session_mock = MagicMock(spec=DesignSession)
    session_mock.project_id = project.id

    task_json = json.dumps({"title": "T", "task_type": "INVALID_TYPE", "priority": "NOPE"})
    text = f"[CREATE_TASK]{task_json}[/CREATE_TASK]"

    service = ArchitectService(db_session)
    actions = await service.execute_action_markers(text, session_mock)
    assert len(actions) == 1
    assert actions[0]["type"] == "task_created"
