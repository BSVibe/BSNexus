"""Tests for the long-term memory injection in agent_chat."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from backend.src.api.agent_chat import _build_memory_context, _build_system_prompt
from backend.src.core.memory import LocalMemoryProvider
from backend.src.models import (
    Agent,
    Project,
    ProjectStatus,
    Tenant,
)
from backend.src.core.tenant_context import DEFAULT_TENANT_ID


async def _seed_tenant(db_session) -> None:
    db_session.add(
        Tenant(
            id=DEFAULT_TENANT_ID,
            name="Test",
            slug="test",
            owner_user_id="test-user",
        )
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
        executor_type="claude_api",
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
async def test_build_memory_context_returns_empty_when_no_records(db_session):
    await _seed_tenant(db_session)
    project = await _make_project(db_session)
    agent = await _make_agent(db_session)

    text = await _build_memory_context(agent.id, project.id, db_session)
    assert text == ""


@pytest.mark.asyncio
async def test_build_memory_context_lists_recent_records(db_session):
    await _seed_tenant(db_session)
    project = await _make_project(db_session)
    agent = await _make_agent(db_session)
    provider = LocalMemoryProvider(db_session)
    await provider.remember(
        project.id, agent.id, category="decision", title="Pick Tailwind",
        content="The team standardized on Tailwind in week 1.",
    )
    await provider.remember(
        project.id, agent.id, category="learning", title="ESM imports",
        content="Vite needs explicit .js suffixes for some libs.",
    )
    await db_session.commit()

    text = await _build_memory_context(agent.id, project.id, db_session)
    assert "Pick Tailwind" in text
    assert "ESM imports" in text
    assert "[decision]" in text


@pytest.mark.asyncio
async def test_system_prompt_includes_memory_section(db_session):
    """Confirm that a non-empty memory_context lands in the system prompt."""
    await _seed_tenant(db_session)
    project = await _make_project(db_session)
    agent = await _make_agent(db_session)

    # Reload project with phases eagerly populated so build_project_context
    # doesn't trip the lazy-load greenlet check.
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    result = await db_session.execute(
        select(Project)
        .where(Project.id == project.id)
        .options(selectinload(Project.phases))
    )
    project_loaded = result.scalar_one()

    prompt = _build_system_prompt(
        agent,
        project_loaded,
        goal_context="",
        all_agents=[agent],
        memory_context="MEMORIES_SENTINEL",
    )
    assert "MEMORIES_SENTINEL" in prompt
