"""Tests for the agent templates API."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from backend.src.core.tenant_context import DEFAULT_TENANT_ID
from backend.src.models import Tenant


@pytest.fixture(autouse=True)
async def _seed_default_tenant(db_session):
    db_session.add(
        Tenant(id=DEFAULT_TENANT_ID, name="Test", slug="test", owner_user_id="test-user")
    )
    await db_session.commit()
    yield


async def test_list_templates_includes_specialists(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/agent-templates")
    assert resp.status_code == 200
    ids = {t["id"] for t in resp.json()}
    assert "specialists" in ids
    assert "startup" in ids


async def test_get_template_specialists(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/agent-templates/specialists")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "specialists"
    assert body["agent_count"] == 4
    names = sorted(a["name"] for a in body["agents"])
    assert names == ["Analyzer", "Designer", "MemoryKeeper", "Planner"]


async def test_get_template_404(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/agent-templates/nonexistent")
    assert resp.status_code == 404


async def test_apply_specialists_template_creates_agents_with_prompts(
    client: AsyncClient, db_session
) -> None:
    from sqlalchemy import select

    from backend.src.models import Agent

    resp = await client.post("/api/v1/agent-templates/specialists/apply")
    assert resp.status_code == 200
    created = resp.json()
    assert len(created) == 4

    # Confirm the system prompts actually landed in the DB.
    result = await db_session.execute(
        select(Agent).where(Agent.tenant_id == DEFAULT_TENANT_ID, Agent.role == "designer")
    )
    designer = result.scalar_one_or_none()
    assert designer is not None
    assert designer.system_prompt and "design/system.bsd" in designer.system_prompt

    result = await db_session.execute(
        select(Agent).where(Agent.tenant_id == DEFAULT_TENANT_ID, Agent.role == "analyzer")
    )
    analyzer = result.scalar_one_or_none()
    assert analyzer is not None
    assert analyzer.system_prompt and "analyzer" in analyzer.system_prompt.lower()


async def test_apply_template_404_for_unknown(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/agent-templates/nope/apply")
    assert resp.status_code == 404
