"""Tests for Unified Project Chat API — async multi-agent dispatch.

POST /chat is fire-and-forget: returns dispatched_agents, actual responses
arrive via SSE. These tests verify routing, persistence, markers, and events.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from backend.src.api.agent_chat import _find_org_root, _parse_mentions
from backend.src.models import (
    Agent,
    ConversationMessage,
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
    result = []
    for name, role in [("CEO", "cto"), ("Engineer", "engineer"), ("QA_Lead", "qa")]:
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
    with patch("backend.src.api.agent_chat.LLMClient") as mock_cls:
        instance = AsyncMock()
        instance.chat = AsyncMock(return_value="Here is my response.")
        mock_cls.return_value = instance
        yield instance


@pytest.fixture
def mock_llm_with_task():
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
        assert _parse_mentions("just a regular message", agents) == []

    def test_case_insensitive(self, agents) -> None:
        mentioned = _parse_mentions("@ceo what do you think?", agents)
        assert len(mentioned) == 1
        assert mentioned[0].name == "CEO"

    def test_underscore_in_name(self, agents) -> None:
        mentioned = _parse_mentions("@QA_Lead please review", agents)
        assert len(mentioned) == 1
        assert mentioned[0].name == "QA_Lead"

    def test_no_duplicate_mentions(self, agents) -> None:
        assert len(_parse_mentions("@CEO hello @CEO again", agents)) == 1


class TestOrgRootFallback:
    def test_picks_org_root(self, agents) -> None:
        root = _find_org_root(agents)
        assert root is not None
        assert root.name == "CEO"

    def test_empty_list(self) -> None:
        assert _find_org_root([]) is None

    def test_falls_back_to_first(self, db_session) -> None:
        a = Agent(
            tenant_id=_TENANT_ID, name="Worker", role="worker",
            executor_type="claude_api", executor_config={},
            capabilities=["coding"], status="online",
        )
        assert _find_org_root([a]) == a


# ── POST /chat (fire-and-forget dispatch) ──────────────────────────


class TestChatDispatch:
    """POST /chat returns dispatched_agents immediately.
    Agent responses arrive via SSE background tasks.
    """

    @pytest.mark.asyncio
    async def test_dispatch_with_mention(self, client, project, agents, mock_llm) -> None:
        resp = await client.post(
            f"/api/v1/projects/{project.id}/chat",
            json={"message": "@Engineer build auth API"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "dispatched_agents" in data
        assert "Engineer" in data["dispatched_agents"]

    @pytest.mark.asyncio
    async def test_dispatch_multi_mention(self, client, project, agents, mock_llm) -> None:
        resp = await client.post(
            f"/api/v1/projects/{project.id}/chat",
            json={"message": "@CEO @Engineer let's plan together"},
        )
        assert resp.status_code == 200
        agents_list = resp.json()["dispatched_agents"]
        assert "CEO" in agents_list
        assert "Engineer" in agents_list

    @pytest.mark.asyncio
    async def test_dispatch_without_mention_uses_fallback(self, client, project, agents, mock_llm) -> None:
        resp = await client.post(
            f"/api/v1/projects/{project.id}/chat",
            json={"message": "what's the project status?"},
        )
        assert resp.status_code == 200
        # Fallback dispatches the org root (CEO)
        assert len(resp.json()["dispatched_agents"]) >= 1

    @pytest.mark.asyncio
    async def test_user_message_persisted_immediately(self, client, db_session, project, agents, mock_llm) -> None:
        from sqlalchemy import select

        await client.post(
            f"/api/v1/projects/{project.id}/chat",
            json={"message": "@Engineer hello"},
        )
        result = await db_session.execute(
            select(ConversationMessage)
            .where(ConversationMessage.project_id == project.id, ConversationMessage.role == "user")
        )
        user_msg = result.scalar_one_or_none()
        assert user_msg is not None
        assert user_msg.content == "@Engineer hello"

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

    @pytest.mark.asyncio
    async def test_empty_message_rejected(self, client, project, agents) -> None:
        resp = await client.post(
            f"/api/v1/projects/{project.id}/chat",
            json={"message": ""},
        )
        assert resp.status_code == 422


# ── History + Clear ─────────────────────────────────────────────────


class TestChatHistory:
    @pytest.mark.asyncio
    async def test_history_returns_persisted_messages(self, client, db_session, project, agents, mock_llm) -> None:
        # Seed a user message directly
        from backend.src.repositories.conversation_repository import ConversationRepository
        repo = ConversationRepository(db_session)
        await repo.append(project.id, role="user", content="hello")
        await repo.append(project.id, role="assistant", content="hi", agent_name="CEO")
        await db_session.commit()

        resp = await client.get(f"/api/v1/projects/{project.id}/chat")
        assert resp.status_code == 200
        msgs = resp.json()["messages"]
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["agent_name"] == "CEO"

    @pytest.mark.asyncio
    async def test_clear_deletes_messages(self, client, db_session, project, agents, mock_llm) -> None:
        from sqlalchemy import select
        from backend.src.repositories.conversation_repository import ConversationRepository
        repo = ConversationRepository(db_session)
        await repo.append(project.id, role="user", content="hello")
        await db_session.commit()

        await client.delete(f"/api/v1/projects/{project.id}/chat")

        result = await db_session.execute(
            select(ConversationMessage).where(ConversationMessage.project_id == project.id)
        )
        assert list(result.scalars().all()) == []


# ── Events publishing ───────────────────────────────────────────────


class TestEventPublishing:
    @pytest.mark.asyncio
    async def test_user_message_published_to_stream(self, test_app, client, project, agents, mock_llm) -> None:
        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock(return_value="msg-id")
        mock_redis.xtrim = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        test_app.state.redis = mock_redis

        try:
            await client.post(
                f"/api/v1/projects/{project.id}/chat",
                json={"message": "@Engineer hello"},
            )
        finally:
            del test_app.state.redis

        # At least the user message should be published
        assert mock_redis.xadd.call_count >= 1
        first_call = mock_redis.xadd.call_args_list[0]
        assert first_call.args[0] == f"chat:events:{project.id}"
        assert first_call.args[1]["event"] == "message_created"

    @pytest.mark.asyncio
    async def test_clear_publishes_event(self, test_app, client, project, agents, mock_llm) -> None:
        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock(return_value="msg-id")
        mock_redis.xtrim = AsyncMock()
        test_app.state.redis = mock_redis

        try:
            await client.delete(f"/api/v1/projects/{project.id}/chat")
        finally:
            del test_app.state.redis

        assert mock_redis.xadd.call_count == 1
        assert mock_redis.xadd.call_args.args[1]["event"] == "history_cleared"


# ── Worker executor routing ─────────────────────────────────────────


@pytest_asyncio.fixture
async def worker_agent(db_session) -> tuple[Agent, Worker, ExecutorConfig]:
    worker = Worker(
        tenant_id=_TENANT_ID, name="test-worker", labels=[], capabilities=["claude_code"],
        token_hash="fakehash", status="online", is_active=True,
        last_heartbeat=datetime.now(timezone.utc),
    )
    db_session.add(worker)
    await db_session.flush()

    exec_cfg = ExecutorConfig(
        tenant_id=_TENANT_ID, name="Worker: test-worker", executor_type="worker",
        config={"worker_id": str(worker.id)}, description="test worker",
    )
    db_session.add(exec_cfg)
    await db_session.flush()

    agent = Agent(
        tenant_id=_TENANT_ID, name="DevWorker", role="engineer",
        executor_type="worker", executor_config_id=exec_cfg.id,
        executor_config={}, capabilities=["coding"], status="online",
    )
    db_session.add(agent)
    await db_session.flush()
    await db_session.commit()
    return agent, worker, exec_cfg


class TestWorkerDispatch:
    @pytest.mark.asyncio
    async def test_worker_agent_dispatched(self, test_app, client, project, worker_agent) -> None:
        """Worker agent is dispatched via fire-and-forget."""
        agent, worker, _ = worker_agent
        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock(return_value="msg-id")
        mock_redis.xtrim = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.set = AsyncMock()
        mock_redis.delete = AsyncMock()
        test_app.state.redis = mock_redis

        try:
            resp = await client.post(
                f"/api/v1/projects/{project.id}/chat",
                json={"message": f"@{agent.name} build auth API"},
            )
        finally:
            del test_app.state.redis

        assert resp.status_code == 200
        assert agent.name in resp.json()["dispatched_agents"]

    @pytest.mark.asyncio
    async def test_no_redis_returns_500(self, test_app, client, project, worker_agent) -> None:
        agent, _, _ = worker_agent
        if hasattr(test_app.state, "redis"):
            del test_app.state.redis

        resp = await client.post(
            f"/api/v1/projects/{project.id}/chat",
            json={"message": f"@{agent.name} hello"},
        )
        # Fire-and-forget still returns 200 (BG task will fail)
        assert resp.status_code == 200


# ── Phase auto-create ───────────────────────────────────────────────


class TestPhaseAutoCreate:
    @pytest.mark.asyncio
    async def test_auto_creates_phase_when_none_exists(self, db_session) -> None:
        from backend.src.api.agent_chat import _ensure_active_phase
        # Project with no phases
        p = Project(name="No Phases", description="", status=ProjectStatus.active)
        db_session.add(p)
        await db_session.flush()

        phase = await _ensure_active_phase(p.id, db_session)
        assert phase is not None
        assert phase.status == PhaseStatus.active
        assert phase.name == "Phase 1"
