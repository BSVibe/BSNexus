"""Tests for org-mission injection into the agent_chat system prompt."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from backend.src.api.agent_chat import _build_org_context, _build_system_prompt
from backend.src.core.tenant_context import DEFAULT_TENANT_ID
from backend.src.models import Agent, Goal, Project, ProjectStatus, Tenant


async def _seed_tenant(db_session) -> None:
    db_session.add(
        Tenant(id=DEFAULT_TENANT_ID, name="Test", slug="test", owner_user_id="test-user")
    )
    await db_session.commit()


async def _make_project(db_session) -> Project:
    now = datetime.now(timezone.utc)
    project = Project(
        id=uuid.uuid4(),
        name="P",
        description="",
        status=ProjectStatus.active,
        created_at=now,
        updated_at=now,
    )
    db_session.add(project)
    await db_session.flush()
    return project


async def _make_agent(db_session) -> Agent:
    now = datetime.now(timezone.utc)
    agent = Agent(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="Tester",
        role="dev",
        executor_type="generic_llm",
        executor_config={},
        capabilities=[],
        status="online",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(agent)
    await db_session.flush()
    return agent


@pytest.mark.asyncio
async def test_build_org_context_returns_empty_when_no_mission_goals(db_session):
    await _seed_tenant(db_session)
    text = await _build_org_context(DEFAULT_TENANT_ID, db_session)
    assert text == ""


@pytest.mark.asyncio
async def test_build_org_context_lists_mission_goals(db_session):
    await _seed_tenant(db_session)
    db_session.add(
        Goal(
            id=uuid.uuid4(),
            tenant_id=DEFAULT_TENANT_ID,
            level="mission",
            title="Build the AI Company OS",
            description="Every product flows through this north star.",
        )
    )
    db_session.add(
        Goal(
            id=uuid.uuid4(),
            tenant_id=DEFAULT_TENANT_ID,
            level="mission",
            title="Be opinionated about defaults",
        )
    )
    # A non-mission goal must NOT leak into the org context.
    db_session.add(
        Goal(
            id=uuid.uuid4(),
            tenant_id=DEFAULT_TENANT_ID,
            level="project",
            title="Ship the plan view",
        )
    )
    await db_session.commit()

    text = await _build_org_context(DEFAULT_TENANT_ID, db_session)
    assert "Build the AI Company OS" in text
    assert "Be opinionated about defaults" in text
    assert "Ship the plan view" not in text
    assert text.startswith("[Organization mission")


@pytest.mark.asyncio
async def test_build_org_context_scopes_to_tenant(db_session):
    """Mission goals from a different tenant must not leak in."""
    await _seed_tenant(db_session)
    other_tenant_id = uuid.uuid4()
    db_session.add(
        Tenant(id=other_tenant_id, name="Other", slug="other", owner_user_id="other-user")
    )
    await db_session.commit()
    db_session.add(
        Goal(
            id=uuid.uuid4(),
            tenant_id=other_tenant_id,
            level="mission",
            title="Other tenant secret",
        )
    )
    await db_session.commit()

    text = await _build_org_context(DEFAULT_TENANT_ID, db_session)
    assert "Other tenant secret" not in text


@pytest.mark.asyncio
async def test_system_prompt_contains_agent_identity(db_session):
    """System prompt includes agent name and project name."""
    await _seed_tenant(db_session)
    project = await _make_project(db_session)
    agent = await _make_agent(db_session)

    result = await db_session.execute(
        select(Project)
        .where(Project.id == project.id)
        .options(selectinload(Project.phases))
    )
    project_loaded = result.scalar_one()

    prompt = await _build_system_prompt(
        agent,
        project_loaded,
        all_agents=[agent],
    )
    assert "Tester" in prompt
