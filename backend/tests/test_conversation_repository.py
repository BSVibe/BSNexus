"""Tests for ConversationRepository — DB-backed chat history."""

from __future__ import annotations

import uuid

import pytest

from backend.src.models import Project, ProjectStatus
from backend.src.repositories.conversation_repository import ConversationRepository

pytestmark = pytest.mark.asyncio


async def _make_project(db_session) -> Project:
    project = Project(name="Chat Repo Project", description="t", status=ProjectStatus.active)
    db_session.add(project)
    await db_session.flush()
    return project


async def test_append_and_list_returns_oldest_first(db_session) -> None:
    project = await _make_project(db_session)
    repo = ConversationRepository(db_session)

    await repo.append(project.id, role="user", content="hi")
    await repo.append(project.id, role="assistant", content="hello", agent_name="CEO")

    msgs = await repo.list_by_project(project.id)
    assert [m.content for m in msgs] == ["hi", "hello"]
    assert [m.role for m in msgs] == ["user", "assistant"]


async def test_append_persists_actions_and_source(db_session) -> None:
    project = await _make_project(db_session)
    repo = ConversationRepository(db_session)

    actions = [{"type": "task_created", "task_id": "abc", "title": "T1"}]
    msg = await repo.append(
        project.id,
        role="assistant",
        content="done",
        agent_name="Engineer",
        actions=actions,
        source="slack",
        external_id="slack-msg-1",
        thread_ref="slack-thread-1",
    )

    assert msg.actions == actions
    assert msg.source == "slack"
    assert msg.external_id == "slack-msg-1"
    assert msg.thread_ref == "slack-thread-1"


async def test_list_limits_and_returns_recent_oldest_first(db_session) -> None:
    project = await _make_project(db_session)
    repo = ConversationRepository(db_session)

    for i in range(10):
        await repo.append(project.id, role="user", content=f"msg-{i}")

    msgs = await repo.list_by_project(project.id, limit=5)
    # Returns the 5 most recent, in oldest-first order
    assert [m.content for m in msgs] == ["msg-5", "msg-6", "msg-7", "msg-8", "msg-9"]


async def test_clear_removes_only_target_project(db_session) -> None:
    project_a = await _make_project(db_session)
    project_b = await _make_project(db_session)
    repo = ConversationRepository(db_session)

    await repo.append(project_a.id, role="user", content="a1")
    await repo.append(project_b.id, role="user", content="b1")

    await repo.clear(project_a.id)

    assert await repo.list_by_project(project_a.id) == []
    remaining_b = await repo.list_by_project(project_b.id)
    assert [m.content for m in remaining_b] == ["b1"]


async def test_list_empty_project_returns_empty_list(db_session) -> None:
    repo = ConversationRepository(db_session)
    msgs = await repo.list_by_project(uuid.uuid4())
    assert msgs == []


async def test_append_accepts_preallocated_id(db_session) -> None:
    """Streaming pre-allocates a UUID before the LLM emits any deltas so
    text_delta events can reference it. The persisted row must use the
    same id so the frontend can match deltas to the final message."""
    project = await _make_project(db_session)
    repo = ConversationRepository(db_session)

    preallocated = uuid.uuid4()
    msg = await repo.append(
        project.id,
        role="assistant",
        content="streamed response",
        message_id=preallocated,
    )
    assert msg.id == preallocated

    msgs = await repo.list_by_project(project.id)
    assert msgs[0].id == preallocated
