"""Unit tests for pure helper functions in agent_chat.

These don't go through the FastAPI client; they exercise the
mention parser, org-root fallback, marker stripping, and message
serialization helpers in isolation so the heavy code paths in
agent_chat.py get coverage credit.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from backend.src.api.agent_chat import (
    _build_system_prompt,
    _find_org_root,
    _msg_to_out,
    _parse_mentions,
    _strip_all_markers,
)
from backend.src.models import Agent, ConversationMessage, Project, ProjectStatus


def _make_agent(name: str, parent_id: uuid.UUID | None = None) -> Agent:
    return Agent(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        name=name,
        role=name.lower(),
        executor_type="generic_llm",
        executor_config={},
        capabilities=[],
        status="online",
        is_active=True,
        parent_agent_id=parent_id,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


# ── _parse_mentions ─────────────────────────────────────────────────


def test_parse_mentions_returns_empty_for_no_match():
    agents = [_make_agent("CTO"), _make_agent("CMO")]
    assert _parse_mentions("Hello there", agents) == []


def test_parse_mentions_finds_single_mention():
    cto = _make_agent("CTO")
    cmo = _make_agent("CMO")
    out = _parse_mentions("Hey @CTO can you check this", [cto, cmo])
    assert [a.id for a in out] == [cto.id]


def test_parse_mentions_preserves_appearance_order():
    cto = _make_agent("CTO")
    cmo = _make_agent("CMO")
    qa = _make_agent("QA")
    out = _parse_mentions("@QA then @CTO and finally @CMO", [cto, cmo, qa])
    assert [a.name for a in out] == ["QA", "CTO", "CMO"]


def test_parse_mentions_dedupes_repeats():
    cto = _make_agent("CTO")
    out = _parse_mentions("@CTO ping @CTO again", [cto])
    assert [a.id for a in out] == [cto.id]


def test_parse_mentions_is_case_insensitive():
    cto = _make_agent("CTO")
    out = _parse_mentions("@cto please look", [cto])
    assert out and out[0].id == cto.id


def test_parse_mentions_processes_longer_names_first_for_dedup():
    """sorted descending by name length so PMOps is checked before PM."""
    pm = _make_agent("PM")
    pmops = _make_agent("PMOps")
    out = _parse_mentions("@PMOps please coordinate", [pm, pmops])
    # PMOps must come first in the result; PM may or may not appear
    # depending on substring behavior, but PMOps wins.
    assert out and out[0].name == "PMOps"


# ── _find_org_root ──────────────────────────────────────────────────


def test_find_org_root_returns_none_for_empty_list():
    assert _find_org_root([]) is None


def test_find_org_root_returns_first_root_agent():
    root = _make_agent("CEO")
    child = _make_agent("CTO", parent_id=root.id)
    assert _find_org_root([child, root]) is root


def test_find_org_root_falls_back_to_first_when_no_root():
    """Pathological case: every agent has a parent. Pick the first."""
    a = _make_agent("A", parent_id=uuid.uuid4())
    b = _make_agent("B", parent_id=uuid.uuid4())
    assert _find_org_root([a, b]) is a


# ── _strip_all_markers ──────────────────────────────────────────────


def test_strip_all_markers_removes_create_task_block():
    text = (
        "Sure, I'll create that.\n"
        '[CREATE_TASK]{"title": "Do thing", "priority": "high"}[/CREATE_TASK]\n'
        "Done."
    )
    cleaned = _strip_all_markers(text)
    assert "CREATE_TASK" not in cleaned
    assert "Sure" in cleaned
    assert "Done" in cleaned


def test_strip_all_markers_removes_set_goal_block():
    text = "Setting goal\n[SET_GOAL]{\"title\": \"X\"}[/SET_GOAL]"
    cleaned = _strip_all_markers(text)
    assert "SET_GOAL" not in cleaned
    assert "Setting goal" in cleaned


def test_strip_all_markers_strips_leading_bracket_label():
    """Agent prefixes like '[CPO] response...' should be stripped."""
    cleaned = _strip_all_markers("[CPO] My response here")
    assert cleaned == "My response here"


# ── _msg_to_out ─────────────────────────────────────────────────────


def test_msg_to_out_serializes_actions():
    msg = ConversationMessage(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        role="assistant",
        content="hi",
        agent_id=uuid.uuid4(),
        agent_name="CTO",
        actions=[{"type": "task_created", "task_id": "t1"}],
        created_at=datetime.now(timezone.utc),
    )
    out = _msg_to_out(msg)
    assert out.role == "assistant"
    assert out.agent_name == "CTO"
    assert out.actions == [{"type": "task_created", "task_id": "t1"}]


def test_msg_to_out_handles_none_actions():
    msg = ConversationMessage(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        role="user",
        content="hello",
        agent_id=None,
        agent_name=None,
        actions=None,
        created_at=datetime.now(timezone.utc),
    )
    out = _msg_to_out(msg)
    assert out.actions == []


# ── _build_system_prompt ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_system_prompt_contains_role_and_project_name(db_session):
    """End-to-end string assembly happens via _build_system_prompt."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from backend.src.core.tenant_context import DEFAULT_TENANT_ID
    from backend.src.models import Tenant

    db_session.add(
        Tenant(id=DEFAULT_TENANT_ID, name="Test", slug="test", owner_user_id="test-user")
    )
    await db_session.commit()

    project = Project(
        id=uuid.uuid4(),
        name="Acme Tax",
        description="Tax SaaS for accountants",
        status=ProjectStatus.active,
    )
    db_session.add(project)
    await db_session.commit()

    result = await db_session.execute(
        select(Project).where(Project.id == project.id).options(selectinload(Project.phases))
    )
    project_loaded = result.scalar_one()

    agent = _make_agent("CPO")
    agent.job_description = "Owns product roadmap"
    agent.system_prompt = "Always think about user value first."
    agent.tenant_id = DEFAULT_TENANT_ID

    prompt = await _build_system_prompt(
        agent,
        project_loaded,
        all_agents=[agent],
    )
    assert "CPO" in prompt
    assert "Acme Tax" in prompt
    assert "Always think about user value first." in prompt
    assert "Owns product roadmap" in prompt
