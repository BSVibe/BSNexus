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


async def test_list_templates_includes_role_based(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/agent-templates")
    assert resp.status_code == 200
    ids = {t["id"] for t in resp.json()}
    assert "startup" in ids
    assert "minimal" in ids
    assert "enterprise" in ids
    # The legacy Specialists template was replaced by per-role skill assignments.
    assert "specialists" not in ids


async def test_get_template_startup(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/agent-templates/startup")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "startup"
    assert body["agent_count"] >= 1


async def test_get_template_404(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/agent-templates/nonexistent")
    assert resp.status_code == 404


async def test_apply_startup_template_assigns_skill_capabilities(
    client: AsyncClient, db_session
) -> None:
    """Template agents must come out with the capability tokens that map to skills.

    The mapping ``capability → skill`` happens at chat dispatch time, so the
    only thing the template has to get right is putting ``plan`` /
    ``analyze`` / ``design`` into ``capabilities`` for the agents that
    should hold those skills. Custom agents created via the Hire Agent
    form follow the same path.
    """
    from sqlalchemy import select

    from backend.src.models import Agent
    from backend.src.prompts.skills import derive_skills_from_capabilities

    resp = await client.post("/api/v1/agent-templates/startup/apply")
    assert resp.status_code == 200

    result = await db_session.execute(
        select(Agent).where(Agent.tenant_id == DEFAULT_TENANT_ID, Agent.role == "cto")
    )
    cto = result.scalar_one_or_none()
    assert cto is not None
    assert "plan" in cto.capabilities
    assert "analyze" in cto.capabilities
    derived = derive_skills_from_capabilities(cto.capabilities)
    assert "plan" in derived
    assert "analyze" in derived
    assert "memory_keeping" in derived

    # The Designer in the same template should have the design capability
    # (and therefore the design skill) but NOT plan/analyze.
    result = await db_session.execute(
        select(Agent).where(Agent.tenant_id == DEFAULT_TENANT_ID, Agent.role == "designer")
    )
    designer = result.scalar_one_or_none()
    assert designer is not None
    assert "design" in designer.capabilities
    derived = derive_skills_from_capabilities(designer.capabilities)
    assert "design" in derived
    assert "memory_keeping" in derived
    assert "plan" not in derived


async def test_apply_template_404_for_unknown(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/agent-templates/nope/apply")
    assert resp.status_code == 404


def test_ceo_templates_have_system_prompt() -> None:
    """CEO templates must include a system_prompt for natural language dispatch."""
    from backend.src.api.agent_templates import TEMPLATES

    for template_id in ("startup", "enterprise"):
        template = TEMPLATES[template_id]
        ceo = template.agents[0]
        assert ceo.role == "ceo", f"{template_id} root agent should be CEO"
        assert ceo.system_prompt is not None, f"{template_id} CEO missing system_prompt"
        assert "create_phase" in ceo.system_prompt
        assert "create_task" in ceo.system_prompt
        assert "@mention" in ceo.system_prompt
