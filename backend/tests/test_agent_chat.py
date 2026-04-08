"""Tests for Unified Project Chat API — @mention routing + goal markers + executor routing."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from backend.src.api.agent_chat import _parse_mentions, _pick_default_agent
from backend.src.models import (
    Agent,
    ExecutorConfig,
    Phase,
    PhaseStatus,
    Project,
    ProjectStatus,
    Setting,
    Tenant,
)
from backend.src.models.worker import Worker

_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


@pytest_asyncio.fixture(autouse=True)
async def _seed_tenant(db_session):
    """Ensure default tenant + LLM key exist."""
    t = Tenant(id=_TENANT_ID, name="Test", slug="test", owner_user_id="user-1")
    db_session.add(t)
    db_session.add(Setting(key="llm_api_key", value="sk-test-fake-key-12345678"))
    db_session.add(Setting(key="llm_model", value="anthropic/claude-sonnet-4-20250514"))
    await db_session.flush()
    await db_session.commit()


@pytest_asyncio.fixture
async def project(db_session) -> Project:
    p = Project(name="Test Project", description="A test project", status=ProjectStatus.active)
    db_session.add(p)
    await db_session.flush()
    phase = Phase(
        project_id=p.id, name="Phase 1", description="First phase",
        branch_name="phase/phase-1", order=1, status=PhaseStatus.active,
    )
    db_session.add(phase)
    await db_session.flush()
    await db_session.commit()
    return p


@pytest_asyncio.fixture
async def agents(db_session) -> list[Agent]:
    """Create test agents: CEO, Engineer, QA."""
    result = []
    for name, role in [("CEO", "cto"), ("Engineer", "engineer"), ("QA Lead", "qa")]:
        a = Agent(
            tenant_id=_TENANT_ID, name=name, role=role,
            executor_type="claude_api", executor_config={},
            capabilities=["coding"], status="online",
        )
        db_session.add(a)
        result.append(a)
    await db_session.flush()
    await db_session.commit()
    return result


@pytest.fixture
def mock_llm():
    """Mock LLMClient.chat to return a canned response."""
    with patch("backend.src.api.agent_chat.LLMClient") as mock_cls:
        instance = AsyncMock()
        instance.chat = AsyncMock(return_value="Here is my response.")
        mock_cls.return_value = instance
        yield instance


@pytest.fixture
def mock_llm_with_task():
    """Mock LLM returning a CREATE_TASK marker."""
    response = (
        'Creating the task now.\n\n'
        '[CREATE_TASK]{"title": "Implement Auth API", "description": "JWT endpoints", '
        '"priority": "high", "task_type": "feature"}[/CREATE_TASK]\n\nDone!'
    )
    with patch("backend.src.api.agent_chat.LLMClient") as mock_cls:
        instance = AsyncMock()
        instance.chat = AsyncMock(return_value=response)
        mock_cls.return_value = instance
        yield instance


@pytest.fixture
def mock_llm_with_goal():
    """Mock LLM returning a SET_GOAL marker."""
    response = (
        'Setting the project goal.\n\n'
        '[SET_GOAL]{"title": "Ship auth v2 by Q3", "level": "project", '
        '"description": "Complete auth system with OAuth + JWT"}[/SET_GOAL]\n\nGoal set!'
    )
    with patch("backend.src.api.agent_chat.LLMClient") as mock_cls:
        instance = AsyncMock()
        instance.chat = AsyncMock(return_value=response)
        mock_cls.return_value = instance
        yield instance


# ── @Mention parsing (unit tests) ──────────────────────────────────


class TestMentionParsing:
    def test_single_mention(self, agents) -> None:
        mentioned = _parse_mentions("@Engineer build the auth API", agents)
        assert len(mentioned) == 1
        assert mentioned[0].name == "Engineer"

    def test_multiple_mentions(self, agents) -> None:
        mentioned = _parse_mentions("@CEO @Engineer let's discuss", agents)
        assert len(mentioned) == 2
        assert mentioned[0].name == "CEO"
        assert mentioned[1].name == "Engineer"

    def test_no_mention(self, agents) -> None:
        mentioned = _parse_mentions("just a regular message", agents)
        assert len(mentioned) == 0

    def test_case_insensitive(self, agents) -> None:
        mentioned = _parse_mentions("@ceo what do you think?", agents)
        assert len(mentioned) == 1
        assert mentioned[0].name == "CEO"

    def test_mention_with_space_in_name(self, agents) -> None:
        mentioned = _parse_mentions("@QA Lead please review", agents)
        assert len(mentioned) == 1
        assert mentioned[0].name == "QA Lead"

    def test_no_duplicate_mentions(self, agents) -> None:
        mentioned = _parse_mentions("@CEO hello @CEO again", agents)
        assert len(mentioned) == 1


class TestDefaultAgentPicker:
    def test_prefers_pm_cto(self, agents) -> None:
        default = _pick_default_agent(agents)
        assert default is not None
        assert default.name == "CEO"  # role="cto"

    def test_empty_list(self) -> None:
        assert _pick_default_agent([]) is None

    def test_falls_back_to_first(self, db_session) -> None:
        a = Agent(
            tenant_id=_TENANT_ID, name="Worker", role="worker",
            executor_type="claude_api", executor_config={},
            capabilities=["coding"], status="online",
        )
        assert _pick_default_agent([a]) == a


# ── Chat endpoint tests ────────────────────────────────────────────


class TestUnifiedChat:
    @pytest.mark.asyncio
    async def test_send_with_mention(self, client, project, agents, mock_llm) -> None:
        resp = await client.post(
            f"/api/v1/projects/{project.id}/chat",
            json={"message": "@Engineer build auth API"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["messages"]) == 1
        msg = data["messages"][0]
        assert msg["role"] == "assistant"
        assert msg["agent_name"] == "Engineer"
        assert msg["agent_id"] is not None

    @pytest.mark.asyncio
    async def test_send_without_mention_uses_default(self, client, project, agents, mock_llm) -> None:
        resp = await client.post(
            f"/api/v1/projects/{project.id}/chat",
            json={"message": "what's the project status?"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["messages"]) == 1
        # CEO is default (role=cto)
        assert data["messages"][0]["agent_name"] == "CEO"

    @pytest.mark.asyncio
    async def test_multi_mention(self, client, project, agents, mock_llm) -> None:
        resp = await client.post(
            f"/api/v1/projects/{project.id}/chat",
            json={"message": "@CEO @Engineer let's plan together"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["messages"]) == 2
        assert data["messages"][0]["agent_name"] == "CEO"
        assert data["messages"][1]["agent_name"] == "Engineer"

    @pytest.mark.asyncio
    async def test_task_creation_marker(self, client, project, agents, mock_llm_with_task) -> None:
        resp = await client.post(
            f"/api/v1/projects/{project.id}/chat",
            json={"message": "@Engineer create auth task"},
        )
        assert resp.status_code == 200
        msg = resp.json()["messages"][0]
        assert len(msg["actions"]) == 1
        assert msg["actions"][0]["type"] == "task_created"
        assert msg["actions"][0]["title"] == "Implement Auth API"
        assert "[CREATE_TASK]" not in msg["content"]

    @pytest.mark.asyncio
    async def test_goal_creation_marker(self, client, project, agents, mock_llm_with_goal) -> None:
        resp = await client.post(
            f"/api/v1/projects/{project.id}/chat",
            json={"message": "@CEO set our project goal"},
        )
        assert resp.status_code == 200
        msg = resp.json()["messages"][0]
        goal_actions = [a for a in msg["actions"] if a["type"] == "goal_created"]
        assert len(goal_actions) == 1
        assert goal_actions[0]["title"] == "Ship auth v2 by Q3"
        assert "[SET_GOAL]" not in msg["content"]

    @pytest.mark.asyncio
    async def test_goal_upsert(self, client, project, agents, mock_llm_with_goal) -> None:
        """Second SET_GOAL with same level updates existing goal."""
        await client.post(
            f"/api/v1/projects/{project.id}/chat",
            json={"message": "@CEO set goal"},
        )
        # Second call should update, not create
        resp = await client.post(
            f"/api/v1/projects/{project.id}/chat",
            json={"message": "@CEO update goal"},
        )
        assert resp.status_code == 200
        msg = resp.json()["messages"][0]
        goal_actions = [a for a in msg["actions"] if a["type"] == "goal_updated"]
        assert len(goal_actions) == 1

    @pytest.mark.asyncio
    async def test_history(self, client, project, agents, mock_llm) -> None:
        await client.post(
            f"/api/v1/projects/{project.id}/chat",
            json={"message": "@Engineer hello"},
        )
        resp = await client.get(f"/api/v1/projects/{project.id}/chat")
        assert resp.status_code == 200
        msgs = resp.json()["messages"]
        assert len(msgs) == 2  # user + assistant
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"
        assert msgs[1]["agent_name"] == "Engineer"

    @pytest.mark.asyncio
    async def test_clear_history(self, client, project, agents, mock_llm) -> None:
        await client.post(
            f"/api/v1/projects/{project.id}/chat",
            json={"message": "@Engineer hello"},
        )
        resp = await client.delete(f"/api/v1/projects/{project.id}/chat")
        assert resp.status_code == 200
        resp = await client.get(f"/api/v1/projects/{project.id}/chat")
        assert resp.json()["messages"] == []

    @pytest.mark.asyncio
    async def test_project_not_found(self, client, agents, mock_llm) -> None:
        resp = await client.post(
            f"/api/v1/projects/{uuid.uuid4()}/chat",
            json={"message": "hello"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_no_agents_returns_400(self, client, project, mock_llm) -> None:
        resp = await client.post(
            f"/api/v1/projects/{project.id}/chat",
            json={"message": "hello"},
        )
        assert resp.status_code == 400
        assert "No agents" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_empty_message_rejected(self, client, project, agents) -> None:
        resp = await client.post(
            f"/api/v1/projects/{project.id}/chat",
            json={"message": ""},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_llm_error_returns_502(self, client, project, agents) -> None:
        with patch("backend.src.api.agent_chat.LLMClient") as mock_cls:
            instance = AsyncMock()
            instance.chat = AsyncMock(side_effect=Exception("LLM timeout"))
            mock_cls.return_value = instance
            resp = await client.post(
                f"/api/v1/projects/{project.id}/chat",
                json={"message": "@Engineer hello"},
            )
            assert resp.status_code == 502


# ── Worker executor routing tests ─────────────────────────────────


@pytest_asyncio.fixture
async def worker_agent(db_session) -> tuple[Agent, Worker, ExecutorConfig]:
    """Create a worker-type agent with linked Worker and ExecutorConfig."""
    worker = Worker(
        tenant_id=_TENANT_ID,
        name="test-worker",
        labels=[],
        capabilities=["claude_code"],
        token_hash="fakehash",
        status="online",
        is_active=True,
        last_heartbeat=datetime.now(timezone.utc),
    )
    db_session.add(worker)
    await db_session.flush()

    exec_cfg = ExecutorConfig(
        tenant_id=_TENANT_ID,
        name="Worker: test-worker",
        executor_type="worker",
        config={"worker_id": str(worker.id)},
        description="Self-hosted worker (claude_code)",
    )
    db_session.add(exec_cfg)
    await db_session.flush()

    agent = Agent(
        tenant_id=_TENANT_ID,
        name="DevWorker",
        role="engineer",
        executor_type="worker",
        executor_config_id=exec_cfg.id,
        executor_config={},
        capabilities=["coding"],
        status="online",
    )
    db_session.add(agent)
    await db_session.flush()
    await db_session.commit()
    return agent, worker, exec_cfg


class TestWorkerExecutorChat:
    """Tests for chat routing through worker executors."""

    @pytest.mark.asyncio
    async def test_worker_chat_dispatches_and_returns(self, test_app, client, project, worker_agent) -> None:
        """Worker agent chat: dispatch → poll Redis → return response."""
        agent, worker, _ = worker_agent
        mock_redis = AsyncMock()

        # Simulate: first poll returns None, second returns the result
        result_payload = json.dumps({
            "success": True,
            "output": "I built the auth API.",
            "error_message": None,
            "worker_id": str(worker.id),
        })
        mock_redis.get = AsyncMock(side_effect=[None, result_payload])
        mock_redis.delete = AsyncMock()
        mock_redis.set = AsyncMock()

        test_app.state.redis = mock_redis

        with patch("backend.src.api.agent_chat.WorkerDispatcher") as mock_dispatcher_cls:
            mock_dispatcher = AsyncMock()
            mock_dispatcher.dispatch_chat = AsyncMock(return_value="msg-123")
            mock_dispatcher_cls.return_value = mock_dispatcher

            resp = await client.post(
                f"/api/v1/projects/{project.id}/chat",
                json={"message": f"@{agent.name} build auth API"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["messages"]) == 1
        msg = data["messages"][0]
        assert msg["agent_name"] == "DevWorker"
        assert "I built the auth API" in msg["content"]
        mock_dispatcher.dispatch_chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_worker_chat_error_returns_502(self, test_app, client, project, worker_agent) -> None:
        """Worker returns success=false → 502 error."""
        agent, worker, _ = worker_agent
        mock_redis = AsyncMock()

        result_payload = json.dumps({
            "success": False,
            "output": "",
            "error_message": "CLI process exited with code 1",
            "worker_id": str(worker.id),
        })
        mock_redis.get = AsyncMock(return_value=result_payload)
        mock_redis.delete = AsyncMock()

        test_app.state.redis = mock_redis

        with patch("backend.src.api.agent_chat.WorkerDispatcher") as mock_dispatcher_cls:
            mock_dispatcher = AsyncMock()
            mock_dispatcher.dispatch_chat = AsyncMock(return_value="msg-123")
            mock_dispatcher_cls.return_value = mock_dispatcher

            resp = await client.post(
                f"/api/v1/projects/{project.id}/chat",
                json={"message": f"@{agent.name} hello"},
            )

        assert resp.status_code == 502
        assert "Worker error" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_worker_offline_returns_503(self, test_app, db_session, client, project, worker_agent) -> None:
        """Offline worker → 503 error."""
        agent, worker, _ = worker_agent
        worker.status = "offline"
        await db_session.commit()

        mock_redis = AsyncMock()
        test_app.state.redis = mock_redis

        resp = await client.post(
            f"/api/v1/projects/{project.id}/chat",
            json={"message": f"@{agent.name} hello"},
        )

        assert resp.status_code == 503
        assert "offline" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_worker_timeout_returns_504(self, test_app, client, project, worker_agent) -> None:
        """Worker never responds → 504 timeout."""
        agent, worker, _ = worker_agent
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)  # Never returns result

        test_app.state.redis = mock_redis

        with (
            patch("backend.src.api.agent_chat.WorkerDispatcher") as mock_dispatcher_cls,
            patch("backend.src.api.agent_chat.asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_dispatcher = AsyncMock()
            mock_dispatcher.dispatch_chat = AsyncMock(return_value="msg-123")
            mock_dispatcher_cls.return_value = mock_dispatcher

            resp = await client.post(
                f"/api/v1/projects/{project.id}/chat",
                json={"message": f"@{agent.name} hello"},
            )

        assert resp.status_code == 504
        assert "timed out" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_worker_chat_with_task_markers(self, test_app, client, project, worker_agent) -> None:
        """Worker response containing [CREATE_TASK] markers creates tasks."""
        agent, worker, _ = worker_agent
        mock_redis = AsyncMock()

        response_with_marker = (
            'Done!\n\n[CREATE_TASK]{"title": "Auth Endpoint", "description": "JWT login", '
            '"priority": "high", "task_type": "feature"}[/CREATE_TASK]'
        )
        result_payload = json.dumps({
            "success": True,
            "output": response_with_marker,
            "error_message": None,
            "worker_id": str(worker.id),
        })
        mock_redis.get = AsyncMock(return_value=result_payload)
        mock_redis.delete = AsyncMock()

        test_app.state.redis = mock_redis

        with patch("backend.src.api.agent_chat.WorkerDispatcher") as mock_dispatcher_cls:
            mock_dispatcher = AsyncMock()
            mock_dispatcher.dispatch_chat = AsyncMock(return_value="msg-123")
            mock_dispatcher_cls.return_value = mock_dispatcher

            resp = await client.post(
                f"/api/v1/projects/{project.id}/chat",
                json={"message": f"@{agent.name} create auth task"},
            )

        assert resp.status_code == 200
        msg = resp.json()["messages"][0]
        assert len(msg["actions"]) == 1
        assert msg["actions"][0]["type"] == "task_created"
        assert msg["actions"][0]["title"] == "Auth Endpoint"
        assert "[CREATE_TASK]" not in msg["content"]

    @pytest.mark.asyncio
    async def test_no_redis_returns_500(self, test_app, client, project, worker_agent) -> None:
        """Worker agent chat without Redis available → 500."""
        agent, _, _ = worker_agent

        # Ensure redis is not set on app state
        if hasattr(test_app.state, "redis"):
            del test_app.state.redis

        resp = await client.post(
            f"/api/v1/projects/{project.id}/chat",
            json={"message": f"@{agent.name} hello"},
        )

        assert resp.status_code == 500
        assert "Redis" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_llm_agent_ignores_redis(self, client, project, agents, mock_llm) -> None:
        """LLM-type agents route through LLMClient, not worker dispatch."""
        resp = await client.post(
            f"/api/v1/projects/{project.id}/chat",
            json={"message": "@Engineer build auth"},
        )
        assert resp.status_code == 200
        msg = resp.json()["messages"][0]
        assert msg["agent_name"] == "Engineer"
        # LLMClient.chat was called (via mock_llm fixture)
        mock_llm.chat.assert_called_once()
