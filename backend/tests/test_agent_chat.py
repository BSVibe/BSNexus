"""Tests for Agent Chat API endpoints."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from backend.src.models import (
    Agent,
    Phase,
    PhaseStatus,
    Project,
    ProjectStatus,
    Setting,
    Task,
    TaskStatus,
    Tenant,
)

_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


@pytest_asyncio.fixture(autouse=True)
async def _seed_tenant(db_session):
    """Ensure default tenant exists."""
    t = Tenant(id=_TENANT_ID, name="Test", slug="test", owner_user_id="user-1")
    db_session.add(t)
    # Add global LLM API key setting
    db_session.add(Setting(key="llm_api_key", value="sk-test-fake-key-12345678"))
    db_session.add(Setting(key="llm_model", value="anthropic/claude-sonnet-4-20250514"))
    await db_session.flush()
    await db_session.commit()


@pytest_asyncio.fixture
async def project(db_session) -> Project:
    """Create a test project with an active phase."""
    p = Project(
        name="Test Project",
        description="A test project for chat",
        status=ProjectStatus.active,
    )
    db_session.add(p)
    await db_session.flush()

    phase = Phase(
        project_id=p.id,
        name="Phase 1",
        description="First phase",
        branch_name="phase/phase-1",
        order=1,
        status=PhaseStatus.active,
    )
    db_session.add(phase)
    await db_session.flush()
    await db_session.commit()
    return p


@pytest_asyncio.fixture
async def agent(db_session) -> Agent:
    """Create a test agent."""
    a = Agent(
        tenant_id=_TENANT_ID,
        name="Test Engineer",
        role="engineer",
        title="Senior Engineer",
        executor_type="claude_api",
        executor_config={},
        system_prompt="You are a helpful engineering assistant.",
        capabilities=["coding"],
        status="online",
    )
    db_session.add(a)
    await db_session.flush()
    await db_session.commit()
    return a


@pytest.fixture
def mock_llm_chat():
    """Mock LLMClient.chat to return a canned response."""
    with patch("backend.src.api.agent_chat.LLMClient") as mock_cls:
        instance = AsyncMock()
        instance.chat = AsyncMock(return_value="Here is my response about the project.")
        mock_cls.return_value = instance
        yield instance


@pytest.fixture
def mock_llm_chat_with_task():
    """Mock LLMClient.chat to return a response with task creation markers."""
    response = (
        'I\'ll create the auth API task for you.\n\n'
        '[CREATE_TASK]{"title": "Implement Auth API", "description": "Build JWT auth endpoints", '
        '"priority": "high", "task_type": "feature", '
        '"worker_prompt": "Implement JWT authentication", "qa_prompt": "Test auth flow"}[/CREATE_TASK]\n\n'
        'The task has been created.'
    )
    with patch("backend.src.api.agent_chat.LLMClient") as mock_cls:
        instance = AsyncMock()
        instance.chat = AsyncMock(return_value=response)
        mock_cls.return_value = instance
        yield instance


class TestAgentChat:
    @pytest.mark.asyncio
    async def test_send_message(self, client, project, agent, mock_llm_chat) -> None:
        resp = await client.post(
            f"/api/v1/projects/{project.id}/chat",
            json={"agent_id": str(agent.id), "message": "Hello, how is the project going?"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["message"]["role"] == "assistant"
        assert data["message"]["content"] == "Here is my response about the project."
        assert data["message"]["actions"] == []
        mock_llm_chat.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_message_creates_task(self, client, project, agent, mock_llm_chat_with_task) -> None:
        resp = await client.post(
            f"/api/v1/projects/{project.id}/chat",
            json={"agent_id": str(agent.id), "message": "Create an auth API task"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["message"]["actions"]) == 1
        assert data["message"]["actions"][0]["type"] == "task_created"
        assert data["message"]["actions"][0]["title"] == "Implement Auth API"
        # Markers should be stripped from display text
        assert "[CREATE_TASK]" not in data["message"]["content"]
        assert "created" in data["message"]["content"].lower() or "auth" in data["message"]["content"].lower()

    @pytest.mark.asyncio
    async def test_chat_history(self, client, project, agent, mock_llm_chat) -> None:
        # Send a message first
        await client.post(
            f"/api/v1/projects/{project.id}/chat",
            json={"agent_id": str(agent.id), "message": "Hello"},
        )

        # Get history
        resp = await client.get(
            f"/api/v1/projects/{project.id}/chat",
            params={"agent_id": str(agent.id)},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["messages"]) == 2  # user + assistant
        assert data["messages"][0]["role"] == "user"
        assert data["messages"][0]["content"] == "Hello"
        assert data["messages"][1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_clear_history(self, client, project, agent, mock_llm_chat) -> None:
        # Send a message
        await client.post(
            f"/api/v1/projects/{project.id}/chat",
            json={"agent_id": str(agent.id), "message": "Hello"},
        )

        # Clear
        resp = await client.delete(
            f"/api/v1/projects/{project.id}/chat",
            params={"agent_id": str(agent.id)},
        )
        assert resp.status_code == 200

        # Verify empty
        resp = await client.get(
            f"/api/v1/projects/{project.id}/chat",
            params={"agent_id": str(agent.id)},
        )
        assert resp.json()["messages"] == []

    @pytest.mark.asyncio
    async def test_project_not_found(self, client, agent, mock_llm_chat) -> None:
        fake_id = uuid.uuid4()
        resp = await client.post(
            f"/api/v1/projects/{fake_id}/chat",
            json={"agent_id": str(agent.id), "message": "Hello"},
        )
        assert resp.status_code == 404
        assert "Project not found" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_agent_not_found(self, client, project, mock_llm_chat) -> None:
        fake_id = uuid.uuid4()
        resp = await client.post(
            f"/api/v1/projects/{project.id}/chat",
            json={"agent_id": str(fake_id), "message": "Hello"},
        )
        assert resp.status_code == 404
        assert "Agent not found" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_empty_message_rejected(self, client, project, agent) -> None:
        resp = await client.post(
            f"/api/v1/projects/{project.id}/chat",
            json={"agent_id": str(agent.id), "message": ""},
        )
        assert resp.status_code == 422  # Validation error

    @pytest.mark.asyncio
    async def test_llm_error_returns_502(self, client, project, agent) -> None:
        with patch("backend.src.api.agent_chat.LLMClient") as mock_cls:
            instance = AsyncMock()
            instance.chat = AsyncMock(side_effect=Exception("LLM provider timeout"))
            mock_cls.return_value = instance

            resp = await client.post(
                f"/api/v1/projects/{project.id}/chat",
                json={"agent_id": str(agent.id), "message": "Hello"},
            )
            assert resp.status_code == 502
            assert "LLM error" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_conversation_context_maintained(self, client, project, agent, mock_llm_chat) -> None:
        """Verify that multiple messages build up conversation context."""
        # First message
        await client.post(
            f"/api/v1/projects/{project.id}/chat",
            json={"agent_id": str(agent.id), "message": "First message"},
        )

        # Second message — LLM should receive full history
        mock_llm_chat.chat.reset_mock()
        await client.post(
            f"/api/v1/projects/{project.id}/chat",
            json={"agent_id": str(agent.id), "message": "Second message"},
        )

        # Check messages sent to LLM include history
        call_args = mock_llm_chat.chat.call_args
        messages = call_args[0][0]  # first positional arg
        # Should have: system + user1 + assistant1 + user2
        assert len(messages) >= 4
        assert messages[0]["role"] == "system"
        user_messages = [m for m in messages if m["role"] == "user"]
        assert len(user_messages) == 2

    @pytest.mark.asyncio
    async def test_no_llm_key_returns_400(self, client, project, agent, db_session) -> None:
        """If no LLM key is configured, return a helpful error."""
        # Remove the LLM key
        from sqlalchemy import delete
        await db_session.execute(delete(Setting).where(Setting.key == "llm_api_key"))
        await db_session.commit()

        with patch("backend.src.api.agent_chat.LLMClient"):
            resp = await client.post(
                f"/api/v1/projects/{project.id}/chat",
                json={"agent_id": str(agent.id), "message": "Hello"},
            )
            assert resp.status_code == 400
            assert "API key" in resp.json()["detail"]


class TestActionMarkerParsing:
    @pytest.mark.asyncio
    async def test_multiple_tasks_created(self, client, project, agent) -> None:
        """Multiple CREATE_TASK markers create multiple tasks."""
        response = (
            'Creating two tasks:\n'
            '[CREATE_TASK]{"title": "Task A", "priority": "high"}[/CREATE_TASK]\n'
            '[CREATE_TASK]{"title": "Task B", "priority": "low", "task_type": "bug"}[/CREATE_TASK]\n'
            'Done!'
        )
        with patch("backend.src.api.agent_chat.LLMClient") as mock_cls:
            instance = AsyncMock()
            instance.chat = AsyncMock(return_value=response)
            mock_cls.return_value = instance

            resp = await client.post(
                f"/api/v1/projects/{project.id}/chat",
                json={"agent_id": str(agent.id), "message": "Create tasks"},
            )
            assert resp.status_code == 200
            assert len(resp.json()["message"]["actions"]) == 2
            assert resp.json()["message"]["actions"][0]["title"] == "Task A"
            assert resp.json()["message"]["actions"][1]["title"] == "Task B"

    @pytest.mark.asyncio
    async def test_invalid_json_in_marker_skipped(self, client, project, agent) -> None:
        """Invalid JSON in marker is silently skipped."""
        response = (
            'Creating task:\n'
            '[CREATE_TASK]not valid json[/CREATE_TASK]\n'
            '[CREATE_TASK]{"title": "Valid Task"}[/CREATE_TASK]\n'
            'Done!'
        )
        with patch("backend.src.api.agent_chat.LLMClient") as mock_cls:
            instance = AsyncMock()
            instance.chat = AsyncMock(return_value=response)
            mock_cls.return_value = instance

            resp = await client.post(
                f"/api/v1/projects/{project.id}/chat",
                json={"agent_id": str(agent.id), "message": "Create tasks"},
            )
            assert resp.status_code == 200
            assert len(resp.json()["message"]["actions"]) == 1
            assert resp.json()["message"]["actions"][0]["title"] == "Valid Task"
