"""Tests for Agent CRUD API endpoints."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from backend.src.models import Agent, Tenant

_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


@pytest_asyncio.fixture(autouse=True)
async def _seed_tenant(db_session):
    """Ensure default tenant exists for all agent tests."""
    t = Tenant(id=_TENANT_ID, name="Test", slug="test", owner_user_id="user-1")
    db_session.add(t)
    await db_session.flush()
    await db_session.commit()


@pytest.fixture
def _seed_agents(db_session):
    """Seed test agents for API tests."""

    async def _create(count: int = 3) -> list[Agent]:
        agents = []
        for i in range(count):
            agent = Agent(
                tenant_id=_TENANT_ID,
                name=f"Agent-{i}",
                role=f"role-{i}",
                title=f"Title {i}",
                executor_type="claude_api",
                executor_config={},
                capabilities=["coding"],
                status="online",
            )
            db_session.add(agent)
            agents.append(agent)
        await db_session.flush()
        await db_session.commit()
        return agents

    return _create


class TestAgentCreate:
    @pytest.mark.asyncio
    async def test_create_agent(self, client) -> None:
        # Spaces in names are normalized to underscores so @mentions are unambiguous.
        resp = await client.post("/api/v1/agents", json={
            "name": "CTO Bot",
            "role": "cto",
            "title": "Chief Technology Officer",
            "capabilities": ["plan", "analyze", "coding"],
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "CTO_Bot"
        assert data["role"] == "cto"
        # executor_type resolved from executor_config_id (null → tenant default → claude_api)
        assert data["executor_type"] == "claude_api"
        assert data["executor_config_id"] is None
        assert data["capabilities"] == ["plan", "analyze", "coding"]
        assert data["status"] == "offline"
        assert data["is_active"] is True

    @pytest.mark.asyncio
    async def test_create_agent_minimal(self, client) -> None:
        resp = await client.post("/api/v1/agents", json={
            "name": "Simple Bot",
            "role": "worker",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["executor_type"] == "claude_api"
        assert data["capabilities"] == ["general"]

    @pytest.mark.asyncio
    async def test_create_agent_with_parent(self, client) -> None:
        # Create parent
        resp1 = await client.post("/api/v1/agents", json={"name": "CEO", "role": "ceo"})
        parent_id = resp1.json()["id"]

        # Create child
        resp2 = await client.post("/api/v1/agents", json={
            "name": "CTO",
            "role": "cto",
            "parent_agent_id": parent_id,
        })
        assert resp2.status_code == 201
        assert resp2.json()["parent_agent_id"] == parent_id


class TestAgentList:
    @pytest.mark.asyncio
    async def test_list_agents(self, client, _seed_agents, db_session) -> None:
        await _seed_agents(3)
        resp = await client.get("/api/v1/agents")
        assert resp.status_code == 200
        assert len(resp.json()) == 3

    @pytest.mark.asyncio
    async def test_list_agents_empty(self, client) -> None:
        resp = await client.get("/api/v1/agents")
        assert resp.status_code == 200
        assert resp.json() == []


class TestAgentGet:
    @pytest.mark.asyncio
    async def test_get_agent(self, client) -> None:
        create_resp = await client.post("/api/v1/agents", json={"name": "Bot", "role": "eng"})
        agent_id = create_resp.json()["id"]

        resp = await client.get(f"/api/v1/agents/{agent_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Bot"

    @pytest.mark.asyncio
    async def test_get_agent_not_found(self, client) -> None:
        fake_id = str(uuid.uuid4())
        resp = await client.get(f"/api/v1/agents/{fake_id}")
        assert resp.status_code == 404


class TestAgentUpdate:
    @pytest.mark.asyncio
    async def test_update_agent(self, client) -> None:
        create_resp = await client.post("/api/v1/agents", json={"name": "Bot", "role": "eng"})
        agent_id = create_resp.json()["id"]

        resp = await client.patch(f"/api/v1/agents/{agent_id}", json={
            "name": "Updated Bot",
            "role": "senior-eng",
            "executor_type": "codex",
        })
        assert resp.status_code == 200
        data = resp.json()
        # Name normalization replaces spaces with underscores.
        assert data["name"] == "Updated_Bot"
        assert data["role"] == "senior-eng"
        assert data["executor_type"] == "codex"

    @pytest.mark.asyncio
    async def test_update_agent_partial(self, client) -> None:
        create_resp = await client.post("/api/v1/agents", json={"name": "Bot", "role": "eng"})
        agent_id = create_resp.json()["id"]

        resp = await client.patch(f"/api/v1/agents/{agent_id}", json={"title": "Lead Engineer"})
        assert resp.status_code == 200
        assert resp.json()["title"] == "Lead Engineer"
        assert resp.json()["name"] == "Bot"  # unchanged


class TestAgentDelete:
    @pytest.mark.asyncio
    async def test_delete_agent(self, client) -> None:
        create_resp = await client.post("/api/v1/agents", json={"name": "Bot", "role": "eng"})
        agent_id = create_resp.json()["id"]

        resp = await client.delete(f"/api/v1/agents/{agent_id}")
        assert resp.status_code == 204

        get_resp = await client.get(f"/api/v1/agents/{agent_id}")
        assert get_resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_agent_not_found(self, client) -> None:
        resp = await client.delete(f"/api/v1/agents/{uuid.uuid4()}")
        assert resp.status_code == 404


class TestAgentOrgChart:
    @pytest.mark.asyncio
    async def test_org_chart_empty(self, client) -> None:
        resp = await client.get("/api/v1/agents/org-chart")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_org_chart_hierarchy(self, client) -> None:
        # Create CEO (root)
        ceo = await client.post("/api/v1/agents", json={"name": "CEO", "role": "ceo"})
        ceo_id = ceo.json()["id"]

        # Create CTO under CEO
        await client.post("/api/v1/agents", json={
            "name": "CTO", "role": "cto", "parent_agent_id": ceo_id,
        })

        # Create Engineer under CTO — need CTO id
        agents_resp = await client.get("/api/v1/agents")
        cto = next(a for a in agents_resp.json() if a["role"] == "cto")
        await client.post("/api/v1/agents", json={
            "name": "Eng", "role": "engineer", "parent_agent_id": cto["id"],
        })

        resp = await client.get("/api/v1/agents/org-chart")
        assert resp.status_code == 200
        tree = resp.json()
        assert len(tree) == 1  # One root (CEO)
        assert tree[0]["agent"]["name"] == "CEO"
        assert len(tree[0]["children"]) == 1  # CTO
        assert tree[0]["children"][0]["agent"]["name"] == "CTO"
        assert len(tree[0]["children"][0]["children"]) == 1  # Engineer
