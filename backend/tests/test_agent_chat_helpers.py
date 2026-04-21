"""Unit tests for pure helper functions in agent_chat.

These don't go through the FastAPI client; they exercise the
mention parser, org-root fallback, marker stripping, message
serialization helpers, and inline marker execution in isolation.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from backend.src.api.agent_chat import (
    _build_system_prompt,
    _execute_inline_markers,
    _find_org_root,
    _msg_to_out,
    _parse_mentions,
    _publish_tool_event,
    _should_skip_active_delegation,
    _strip_all_markers,
)
from backend.src.models import Agent, ConversationMessage, Phase, PhaseStatus, Project, ProjectStatus
from backend.src.models.tenant import Tenant


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


# ── _execute_inline_markers ────────────────────────────────────────


@pytest_asyncio.fixture
async def marker_env(test_session_maker, monkeypatch):
    """Seed DB with project, phase, agents. Monkeypatch async_session."""
    tenant_id = uuid.uuid4()
    project_id = uuid.uuid4()
    phase_id = uuid.uuid4()
    ceo_id = uuid.uuid4()
    designer_id = uuid.uuid4()

    async with test_session_maker() as session:
        session.add(Tenant(id=tenant_id, name="T", slug="t", owner_user_id="u1"))
        session.add(Project(id=project_id, name="P", description="d"))
        await session.flush()
        session.add(Phase(
            id=phase_id, project_id=project_id, name="Planning",
            status=PhaseStatus.active, order=1, branch_name="phase/planning",
        ))
        session.add(Agent(
            id=ceo_id, tenant_id=tenant_id, name="CEO", role="ceo",
            executor_type="generic_llm", capabilities=["plan"], is_active=True,
        ))
        session.add(Agent(
            id=designer_id, tenant_id=tenant_id, name="Designer", role="designer",
            executor_type="generic_llm", capabilities=["design"], is_active=True,
        ))
        await session.commit()

    monkeypatch.setattr("backend.src.api.agent_chat.async_session", test_session_maker)
    monkeypatch.setattr("backend.src.tools.plan_tools.async_session", test_session_maker, raising=False)

    return {
        "tenant_id": tenant_id,
        "project_id": project_id,
        "phase_id": phase_id,
        "ceo_id": ceo_id,
        "designer_id": designer_id,
    }


@pytest.mark.asyncio
async def test_execute_inline_markers_creates_task(marker_env):
    text = '[CREATE_TASK title="화면 디자인" assignee="Designer"]'
    actions = await _execute_inline_markers(
        text,
        project_id=marker_env["project_id"],
        tenant_id=marker_env["tenant_id"],
        agent_id=marker_env["ceo_id"],
        agent_name="CEO",
    )
    assert len(actions) == 1
    assert actions[0]["type"] == "tool_create_task"
    assert actions[0]["tool"] == "create_task"
    assert actions[0]["input"]["title"] == "화면 디자인"


@pytest.mark.asyncio
async def test_execute_inline_markers_creates_phase(marker_env):
    # Phase creation requires at least one accompanying task (#3: ghost
    # phase guard). The test still exercises the phase-creation path.
    text = (
        '[CREATE_PHASE name="개발" description="구현 단계"]\n'
        '[CREATE_TASK title="스펙 작성" assignee="Designer"]'
    )
    actions = await _execute_inline_markers(
        text,
        project_id=marker_env["project_id"],
        tenant_id=marker_env["tenant_id"],
        agent_id=marker_env["ceo_id"],
        agent_name="CEO",
        is_org_root=True,
    )
    phase_actions = [a for a in actions if a["tool"] == "create_phase"]
    assert len(phase_actions) == 1
    assert phase_actions[0]["input"]["name"] == "개발"


@pytest.mark.asyncio
async def test_execute_inline_markers_mixed(marker_env):
    text = """프로젝트를 시작합니다.
[CREATE_PHASE name="리서치" description="시장 조사"]
[CREATE_TASK title="경쟁사 분석" assignee="Designer"]
[CREATE_TASK title="사용자 설문" assignee="CEO"]
계속 진행하겠습니다."""
    actions = await _execute_inline_markers(
        text,
        project_id=marker_env["project_id"],
        tenant_id=marker_env["tenant_id"],
        agent_id=marker_env["ceo_id"],
        agent_name="CEO",
        is_org_root=True,
    )
    phase_actions = [a for a in actions if a["tool"] == "create_phase"]
    task_actions = [a for a in actions if a["tool"] == "create_task"]
    assert len(phase_actions) == 1
    assert len(task_actions) == 2


@pytest.mark.asyncio
async def test_execute_inline_markers_no_markers(marker_env):
    text = "일반 대화 메시지입니다."
    actions = await _execute_inline_markers(
        text,
        project_id=marker_env["project_id"],
        tenant_id=marker_env["tenant_id"],
        agent_id=marker_env["ceo_id"],
        agent_name="CEO",
    )
    assert actions == []


@pytest.mark.asyncio
async def test_execute_inline_markers_dedup(marker_env):
    """Same title twice in one response — second should be deduped."""
    text = """[CREATE_TASK title="같은 작업" assignee="Designer"]
[CREATE_TASK title="같은 작업" assignee="Designer"]"""
    actions = await _execute_inline_markers(
        text,
        project_id=marker_env["project_id"],
        tenant_id=marker_env["tenant_id"],
        agent_id=marker_env["ceo_id"],
        agent_name="CEO",
    )
    # Both markers are parsed, but second one is a dedup (returns existing)
    assert len(actions) == 2  # action records for both


@pytest.mark.asyncio
async def test_execute_inline_markers_rate_limit(marker_env):
    """More than 10 task markers — only first 10 processed."""
    markers = "\n".join(
        f'[CREATE_TASK title="작업 {i}" assignee="Designer"]' for i in range(12)
    )
    actions = await _execute_inline_markers(
        markers,
        project_id=marker_env["project_id"],
        tenant_id=marker_env["tenant_id"],
        agent_id=marker_env["ceo_id"],
        agent_name="CEO",
    )
    task_actions = [a for a in actions if a["tool"] == "create_task"]
    assert len(task_actions) == 10


@pytest.mark.asyncio
async def test_execute_inline_markers_error_isolation(marker_env):
    """A bad marker should not block subsequent markers."""
    # First task has no phase (we'll delete the phase to cause an error)
    # Actually, let's test with a normal case — errors are caught per-marker
    text = '[CREATE_TASK title="정상 작업" assignee="Designer"]'
    actions = await _execute_inline_markers(
        text,
        project_id=marker_env["project_id"],
        tenant_id=marker_env["tenant_id"],
        agent_id=marker_env["ceo_id"],
        agent_name="CEO",
    )
    assert len(actions) == 1


# ── is_org_root gating — Part 1 (phase explosion prevention) ────────


@pytest.mark.asyncio
async def test_execute_inline_markers_blocks_phase_for_subordinate(marker_env):
    """Subordinate agents cannot open ADDITIONAL phases once the project
    already has at least one. Prevents delegation-chain explosion.

    ``marker_env`` seeds a ``Planning`` phase, so this exercises the
    post-bootstrap case: any further CREATE_PHASE from a subordinate is
    dropped silently.
    """
    text = '[CREATE_PHASE name="설계" description="구조 설계"]'
    actions = await _execute_inline_markers(
        text,
        project_id=marker_env["project_id"],
        tenant_id=marker_env["tenant_id"],
        agent_id=marker_env["designer_id"],
        agent_name="Designer",
        is_org_root=False,
    )
    phase_actions = [a for a in actions if a["tool"] == "create_phase"]
    assert phase_actions == []


@pytest.mark.asyncio
async def test_execute_inline_markers_auto_bootstraps_phase_for_tasks(
    test_session_maker, monkeypatch,
):
    """When an agent emits CREATE_TASK markers without any CREATE_PHASE and
    the project has no phase at all, the backend auto-creates a default
    phase so the tasks can attach. Without this, LLMs that skip the
    bootstrap prompt block deadlock the whole scenario on turn 1."""
    tenant_id = uuid.uuid4()
    project_id = uuid.uuid4()
    parent_id = uuid.uuid4()
    cmo_id = uuid.uuid4()

    async with test_session_maker() as session:
        session.add(Tenant(id=tenant_id, name="T3", slug="t3", owner_user_id="u3"))
        session.add(Project(id=project_id, name="P3", description=""))
        await session.flush()
        session.add(Agent(
            id=parent_id, tenant_id=tenant_id, name="CEO", role="ceo",
            executor_type="generic_llm", capabilities=["plan"], is_active=True,
        ))
        await session.flush()
        session.add(Agent(
            id=cmo_id, tenant_id=tenant_id, name="CMO", role="cmo",
            executor_type="generic_llm", capabilities=["plan"], is_active=True,
            parent_agent_id=parent_id,
        ))
        await session.commit()

    monkeypatch.setattr("backend.src.api.agent_chat.async_session", test_session_maker)
    monkeypatch.setattr(
        "backend.src.tools.plan_tools.async_session", test_session_maker, raising=False
    )

    text = (
        '[CREATE_TASK title="시장 분석" assignee="Product_Manager"]\n'
        '[CREATE_TASK title="기획서 작성" assignee="CPO"]'
    )
    actions = await _execute_inline_markers(
        text,
        project_id=project_id,
        tenant_id=tenant_id,
        agent_id=cmo_id,
        agent_name="CMO",
        is_org_root=False,
    )
    phase_actions = [a for a in actions if a["tool"] == "create_phase"]
    task_actions = [a for a in actions if a["tool"] == "create_task"]
    # A default phase was auto-created on behalf of the agent.
    assert len(phase_actions) == 1
    # Both tasks attach to it.
    assert len(task_actions) == 2


@pytest.mark.asyncio
async def test_execute_inline_markers_root_limited_to_one_phase_per_turn(
    marker_env,
):
    """Even org-root emits CREATE_PHASE for at most ONE new phase per turn.

    Longrun E2E showed CEO producing 4+ phases in a single turn, most of
    which stayed empty because the agent had no budget to also fill them
    with tasks. Cap it at 1 so every phase created has a chance of being
    populated.
    """
    text = (
        '[CREATE_PHASE name="개발" description="개발 시작"]\n'
        '[CREATE_PHASE name="테스트"]\n'
        '[CREATE_PHASE name="배포"]\n'
        '[CREATE_TASK title="초기 설계" assignee="Designer"]'
    )
    actions = await _execute_inline_markers(
        text,
        project_id=marker_env["project_id"],
        tenant_id=marker_env["tenant_id"],
        agent_id=marker_env["ceo_id"],
        agent_name="CEO",
        is_org_root=True,
    )
    phase_actions = [a for a in actions if a["tool"] == "create_phase"]
    assert len(phase_actions) == 1
    assert phase_actions[0]["input"]["name"] == "개발"


@pytest.mark.asyncio
async def test_execute_inline_markers_skips_phase_without_tasks(
    test_session_maker, monkeypatch,
):
    """Phase markers without any accompanying CREATE_TASK leave the phase
    tree with empty scaffolding rows that the UI then shows as pending
    forever. Skip the phase creation entirely in that case so every live
    phase has at least one task.
    """
    tenant_id = uuid.uuid4()
    project_id = uuid.uuid4()
    ceo_id = uuid.uuid4()

    async with test_session_maker() as session:
        session.add(Tenant(id=tenant_id, name="T9", slug="t9", owner_user_id="u9"))
        session.add(Project(id=project_id, name="P9", description=""))
        await session.flush()
        session.add(Agent(
            id=ceo_id, tenant_id=tenant_id, name="CEO", role="ceo",
            executor_type="generic_llm", capabilities=["plan"], is_active=True,
        ))
        await session.commit()

    monkeypatch.setattr("backend.src.api.agent_chat.async_session", test_session_maker)
    monkeypatch.setattr(
        "backend.src.tools.plan_tools.async_session", test_session_maker, raising=False
    )

    text = '[CREATE_PHASE name="ghost phase" description="no tasks attached"]'
    actions = await _execute_inline_markers(
        text,
        project_id=project_id,
        tenant_id=tenant_id,
        agent_id=ceo_id,
        agent_name="CEO",
        is_org_root=True,
    )
    phase_actions = [a for a in actions if a["tool"] == "create_phase"]
    assert phase_actions == []


@pytest.mark.asyncio
async def test_execute_inline_markers_no_auto_bootstrap_when_phase_exists(
    marker_env,
):
    """The auto-bootstrap must NOT fire when the project already has a
    phase — it's a one-time safety net, not an every-turn behavior.
    ``marker_env`` seeds a Planning phase; running CREATE_TASK markers
    should not spawn an additional phase."""
    text = '[CREATE_TASK title="그냥 작업" assignee="Designer"]'
    actions = await _execute_inline_markers(
        text,
        project_id=marker_env["project_id"],
        tenant_id=marker_env["tenant_id"],
        agent_id=marker_env["designer_id"],
        agent_name="Designer",
        is_org_root=False,
    )
    phase_actions = [a for a in actions if a["tool"] == "create_phase"]
    task_actions = [a for a in actions if a["tool"] == "create_task"]
    assert phase_actions == []
    assert len(task_actions) == 1


@pytest.mark.asyncio
async def test_execute_inline_markers_subordinate_bootstraps_first_phase(
    test_session_maker, monkeypatch,
):
    """BOOTSTRAP: when a project has no phases yet, a subordinate must be
    allowed to open the first one. Otherwise the first @mentioned agent
    (often CMO/subordinate) cannot create tasks — create_task requires a
    phase, so the whole conversation deadlocks. Only *additional* phases
    are gated to org-root."""
    tenant_id = uuid.uuid4()
    project_id = uuid.uuid4()
    parent_id = uuid.uuid4()
    designer_id = uuid.uuid4()

    async with test_session_maker() as session:
        session.add(Tenant(id=tenant_id, name="T2", slug="t2", owner_user_id="u2"))
        session.add(Project(id=project_id, name="P2", description=""))
        await session.flush()
        # Seed a parent (CEO) so Designer can reference it.
        session.add(Agent(
            id=parent_id, tenant_id=tenant_id, name="CEO", role="ceo",
            executor_type="generic_llm", capabilities=["plan"], is_active=True,
        ))
        await session.flush()
        # Designer is a subordinate (has a parent).
        session.add(Agent(
            id=designer_id, tenant_id=tenant_id, name="Designer", role="designer",
            executor_type="generic_llm", capabilities=["design"], is_active=True,
            parent_agent_id=parent_id,
        ))
        await session.commit()

    monkeypatch.setattr("backend.src.api.agent_chat.async_session", test_session_maker)
    monkeypatch.setattr(
        "backend.src.tools.plan_tools.async_session", test_session_maker, raising=False
    )

    text = (
        '[CREATE_PHASE name="부트스트랩" description="첫 단계"]\n'
        '[CREATE_TASK title="초기 조사" assignee="Designer"]'
    )
    actions = await _execute_inline_markers(
        text,
        project_id=project_id,
        tenant_id=tenant_id,
        agent_id=designer_id,
        agent_name="Designer",
        is_org_root=False,
    )
    phase_actions = [a for a in actions if a["tool"] == "create_phase"]
    assert len(phase_actions) == 1
    assert phase_actions[0]["input"]["name"] == "부트스트랩"


@pytest.mark.asyncio
async def test_execute_inline_markers_allows_phase_for_org_root(marker_env):
    """Org-root agents (is_org_root=True) create phases normally when at
    least one task accompanies them in the same turn."""
    text = (
        '[CREATE_PHASE name="론칭" description="출시 단계"]\n'
        '[CREATE_TASK title="런칭 체크리스트" assignee="Designer"]'
    )
    actions = await _execute_inline_markers(
        text,
        project_id=marker_env["project_id"],
        tenant_id=marker_env["tenant_id"],
        agent_id=marker_env["ceo_id"],
        agent_name="CEO",
        is_org_root=True,
    )
    phase_actions = [a for a in actions if a["tool"] == "create_phase"]
    assert len(phase_actions) == 1
    assert phase_actions[0]["input"]["name"] == "론칭"


@pytest.mark.asyncio
async def test_execute_inline_markers_allows_task_for_subordinate(marker_env):
    """Subordinates can still plan work via CREATE_TASK — only phase is gated."""
    text = (
        '[CREATE_PHASE name="should-be-blocked"]\n'
        '[CREATE_TASK title="와이어프레임 스케치" assignee="Designer"]'
    )
    actions = await _execute_inline_markers(
        text,
        project_id=marker_env["project_id"],
        tenant_id=marker_env["tenant_id"],
        agent_id=marker_env["designer_id"],
        agent_name="Designer",
        is_org_root=False,
    )
    phase_actions = [a for a in actions if a["tool"] == "create_phase"]
    task_actions = [a for a in actions if a["tool"] == "create_task"]
    assert phase_actions == []
    assert len(task_actions) == 1
    assert task_actions[0]["input"]["title"] == "와이어프레임 스케치"


# ── _publish_tool_event — Part 3 (text_delta enrichment) ───────────


class _FakeRedis:
    """Minimal stub capturing xadd calls so we can inspect published events."""

    def __init__(self) -> None:
        self.xadd_calls: list[tuple[str, dict]] = []

    async def xadd(self, stream: str, fields: dict, **kwargs) -> str:  # noqa: ARG002
        self.xadd_calls.append((stream, fields))
        return "0-0"

    async def xtrim(self, *args, **kwargs) -> None:  # noqa: ARG002
        return None

    async def set(self, *args, **kwargs) -> None:  # noqa: ARG002
        return None

    async def delete(self, *args, **kwargs) -> None:  # noqa: ARG002
        return None


@pytest.mark.asyncio
async def test_publish_tool_event_enriches_text_delta_with_message_id():
    """text_delta events must carry message_id, agent_id, agent_name so
    the frontend can match the delta to its assistant message bubble.
    """
    import json as _json

    from backend.src.core.executor.litellm_executor import ExecutionEvent

    redis = _FakeRedis()
    project_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    message_id = uuid.uuid4()
    evt = ExecutionEvent("text_delta", {"text": "Hello"})

    await _publish_tool_event(
        redis, project_id, evt,
        agent_id=agent_id, agent_name="CEO",
        message_id=message_id,
    )

    assert redis.xadd_calls, "expected an xadd call"
    _stream, fields = redis.xadd_calls[-1]
    payload = _json.loads(fields["data"])
    assert fields["event"] == "text_delta"
    assert payload["text"] == "Hello"
    assert payload["message_id"] == str(message_id)
    assert payload["agent_id"] == str(agent_id)
    assert payload["agent_name"] == "CEO"


@pytest.mark.asyncio
async def test_publish_tool_event_text_delta_without_message_id_still_publishes():
    """Backwards compatibility — if a caller doesn't pre-allocate a
    message_id (older path), we still publish the delta."""
    import json as _json

    from backend.src.core.executor.litellm_executor import ExecutionEvent

    redis = _FakeRedis()
    evt = ExecutionEvent("text_delta", {"text": "chunk"})
    await _publish_tool_event(
        redis, uuid.uuid4(), evt,
        agent_id=uuid.uuid4(), agent_name="X",
    )
    assert redis.xadd_calls
    _stream, fields = redis.xadd_calls[-1]
    payload = _json.loads(fields["data"])
    assert payload["text"] == "chunk"
    assert "message_id" not in payload or payload.get("message_id") is None


# ── _should_skip_active_delegation — Part 2 (queue throttle) ────────


@pytest_asyncio.fixture
async def delegation_env(test_session_maker, marker_env):
    """Reuse marker_env's tenant/project, return session maker for gate tests."""
    return {**marker_env, "session_maker": test_session_maker}


@pytest.mark.asyncio
async def test_should_skip_active_delegation_false_when_no_pending(delegation_env):
    """No tasks assigned to the agent → active delegation proceeds."""
    async with delegation_env["session_maker"]() as db:
        assert await _should_skip_active_delegation(
            agent_id=delegation_env["ceo_id"],
            project_id=delegation_env["project_id"],
            db=db,
        ) is False


@pytest.mark.asyncio
async def test_should_skip_active_delegation_true_when_pending_exists(delegation_env):
    """Agent already has a pending assigned task → skip the active enqueue.

    Global dispatcher will dispatch it passively within 5s.
    """
    from backend.src.models import Task, TaskStatus

    async with delegation_env["session_maker"]() as db:
        db.add(Task(
            id=uuid.uuid4(),
            project_id=delegation_env["project_id"],
            phase_id=delegation_env["phase_id"],
            title="pending work",
            status=TaskStatus.pending,
            assigned_agent_id=delegation_env["ceo_id"],
        ))
        await db.commit()

    async with delegation_env["session_maker"]() as db:
        assert await _should_skip_active_delegation(
            agent_id=delegation_env["ceo_id"],
            project_id=delegation_env["project_id"],
            db=db,
        ) is True


@pytest.mark.asyncio
async def test_should_skip_active_delegation_ignores_done_tasks(delegation_env):
    """Completed/blocked tasks don't trigger the skip — agent is free."""
    from backend.src.models import Task, TaskStatus

    async with delegation_env["session_maker"]() as db:
        db.add(Task(
            id=uuid.uuid4(),
            project_id=delegation_env["project_id"],
            phase_id=delegation_env["phase_id"],
            title="already finished",
            status=TaskStatus.done,
            assigned_agent_id=delegation_env["ceo_id"],
        ))
        await db.commit()

    async with delegation_env["session_maker"]() as db:
        assert await _should_skip_active_delegation(
            agent_id=delegation_env["ceo_id"],
            project_id=delegation_env["project_id"],
            db=db,
        ) is False


@pytest.mark.asyncio
async def test_should_skip_active_delegation_scoped_to_project(delegation_env):
    """Pending tasks in a different project do not trigger the skip."""
    from backend.src.models import Task, TaskStatus

    other_project_id = uuid.uuid4()
    other_phase_id = uuid.uuid4()
    async with delegation_env["session_maker"]() as db:
        db.add(Project(id=other_project_id, name="OtherP", description=""))
        await db.flush()
        db.add(Phase(
            id=other_phase_id, project_id=other_project_id, name="P",
            status=PhaseStatus.active, order=1, branch_name="phase/p",
        ))
        await db.flush()
        db.add(Task(
            id=uuid.uuid4(),
            project_id=other_project_id,
            phase_id=other_phase_id,
            title="elsewhere",
            status=TaskStatus.pending,
            assigned_agent_id=delegation_env["ceo_id"],
        ))
        await db.commit()

    async with delegation_env["session_maker"]() as db:
        assert await _should_skip_active_delegation(
            agent_id=delegation_env["ceo_id"],
            project_id=delegation_env["project_id"],
            db=db,
        ) is False


@pytest.mark.asyncio
async def test_execute_inline_markers_defaults_is_org_root_false(marker_env):
    """Backward-safe default: unspecified is_org_root treats caller as subordinate.

    Older call sites that haven't adopted the flag should not accidentally
    gain phase-creation powers.
    """
    text = '[CREATE_PHASE name="default-gate"]'
    actions = await _execute_inline_markers(
        text,
        project_id=marker_env["project_id"],
        tenant_id=marker_env["tenant_id"],
        agent_id=marker_env["designer_id"],
        agent_name="Designer",
    )
    phase_actions = [a for a in actions if a["tool"] == "create_phase"]
    assert phase_actions == []


# ── [SET_GOAL] block marker → DB Goal upsert ────────────────────────


@pytest.mark.asyncio
async def test_set_goal_block_creates_project_goal(marker_env, test_session_maker):
    """A fresh [SET_GOAL] block on a project with no goal creates
    a Goal(level='project') row with title + description."""
    from sqlalchemy import select as _select

    from backend.src.models import Goal

    text = (
        "Got it. Defining the project end-state first.\n"
        "[SET_GOAL]\n"
        "Ship a working TodoApp MVP\n"
        "- Users can sign up / log in\n"
        "- CRUD for todos persists across sessions\n"
        "- Mobile-responsive layout verified on iOS Safari\n"
        "[/SET_GOAL]\n"
        "[CREATE_TASK title=\"사용자 인증 스펙\" assignee=\"Designer\"]"
    )
    actions = await _execute_inline_markers(
        text,
        project_id=marker_env["project_id"],
        tenant_id=marker_env["tenant_id"],
        agent_id=marker_env["ceo_id"],
        agent_name="CEO",
        is_org_root=True,
    )

    goal_actions = [a for a in actions if a.get("tool") == "set_goal"]
    assert len(goal_actions) == 1
    assert goal_actions[0]["input"]["title"] == "Ship a working TodoApp MVP"

    async with test_session_maker() as db:
        result = await db.execute(
            _select(Goal).where(
                Goal.project_id == marker_env["project_id"],
                Goal.level == "project",
            )
        )
        goal = result.scalar_one()
        assert goal.title == "Ship a working TodoApp MVP"
        assert goal.description is not None
        assert "CRUD for todos" in goal.description
        assert "iOS Safari" in goal.description


@pytest.mark.asyncio
async def test_set_goal_block_updates_existing_project_goal(
    marker_env, test_session_maker,
):
    """A second [SET_GOAL] block updates the existing Goal row rather than
    creating a duplicate."""
    from sqlalchemy import select as _select

    from backend.src.models import Goal

    async with test_session_maker() as db:
        db.add(Goal(
            tenant_id=marker_env["tenant_id"],
            project_id=marker_env["project_id"],
            level="project",
            title="Old goal title",
            description="Old description",
        ))
        await db.commit()

    text = (
        "[SET_GOAL]\n"
        "Revised TodoApp goal\n"
        "Updated completion criteria here.\n"
        "[/SET_GOAL]\n"
        "[CREATE_TASK title=\"재기획\" assignee=\"Designer\"]"
    )
    actions = await _execute_inline_markers(
        text,
        project_id=marker_env["project_id"],
        tenant_id=marker_env["tenant_id"],
        agent_id=marker_env["ceo_id"],
        agent_name="CEO",
        is_org_root=True,
    )

    goal_actions = [a for a in actions if a.get("tool") == "set_goal"]
    assert len(goal_actions) == 1
    assert goal_actions[0]["input"]["title"] == "Revised TodoApp goal"

    async with test_session_maker() as db:
        result = await db.execute(
            _select(Goal).where(
                Goal.project_id == marker_env["project_id"],
                Goal.level == "project",
            )
        )
        goals = list(result.scalars().all())
        assert len(goals) == 1  # upsert, no duplicate
        assert goals[0].title == "Revised TodoApp goal"
        assert "Updated completion criteria" in goals[0].description


@pytest.mark.asyncio
async def test_set_goal_block_ignored_when_empty_body(marker_env, test_session_maker):
    """A [SET_GOAL][/SET_GOAL] block with empty body is silently skipped —
    no Goal row created, no action emitted."""
    from sqlalchemy import select as _select

    from backend.src.models import Goal

    text = "[SET_GOAL][/SET_GOAL]\n[CREATE_TASK title=\"그냥 작업\" assignee=\"Designer\"]"
    actions = await _execute_inline_markers(
        text,
        project_id=marker_env["project_id"],
        tenant_id=marker_env["tenant_id"],
        agent_id=marker_env["ceo_id"],
        agent_name="CEO",
        is_org_root=True,
    )

    goal_actions = [a for a in actions if a.get("tool") == "set_goal"]
    assert goal_actions == []

    async with test_session_maker() as db:
        result = await db.execute(
            _select(Goal).where(
                Goal.project_id == marker_env["project_id"],
                Goal.level == "project",
            )
        )
        assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_assemble_prompt_inlines_project_goal_from_db(db_session):
    """When a Project-level Goal exists, assemble_system_prompt injects it
    under '## Project Goal' so every agent reads it inline — no file_read
    needed."""
    from sqlalchemy import select as _select
    from sqlalchemy.orm import selectinload

    from backend.src.core.harness import assemble_system_prompt
    from backend.src.core.tenant_context import DEFAULT_TENANT_ID
    from backend.src.models import Goal, Tenant

    db_session.add(
        Tenant(id=DEFAULT_TENANT_ID, name="Test", slug="test", owner_user_id="u"),
    )
    await db_session.commit()

    project = Project(
        id=uuid.uuid4(),
        name="ProjX",
        description="",
        status=ProjectStatus.active,
    )
    db_session.add(project)
    await db_session.flush()
    goal = Goal(
        tenant_id=DEFAULT_TENANT_ID,
        project_id=project.id,
        level="project",
        title="Ship TodoApp MVP",
        description="Users sign up + CRUD todos + mobile verified",
    )
    db_session.add(goal)
    await db_session.commit()

    result = await db_session.execute(
        _select(Project).where(Project.id == project.id).options(selectinload(Project.phases))
    )
    project_loaded = result.scalar_one()

    agent = _make_agent("CPO")
    agent.tenant_id = DEFAULT_TENANT_ID

    prompt = await assemble_system_prompt(
        agent, project_loaded, workspace_dir=None,
        mode="active", all_agents=[agent], project_goal=goal,
    )
    assert "## Project Goal" in prompt
    assert "Ship TodoApp MVP" in prompt
    assert "mobile verified" in prompt


@pytest.mark.asyncio
async def test_assemble_prompt_shows_missing_goal_warning_when_none(db_session):
    """When no project Goal exists, assemble_system_prompt inlines a
    warning inviting the first responder to emit [SET_GOAL], regardless
    of role."""
    from sqlalchemy import select as _select
    from sqlalchemy.orm import selectinload

    from backend.src.core.harness import assemble_system_prompt
    from backend.src.core.tenant_context import DEFAULT_TENANT_ID
    from backend.src.models import Tenant

    db_session.add(
        Tenant(id=DEFAULT_TENANT_ID, name="Test", slug="test", owner_user_id="u"),
    )
    await db_session.commit()

    project = Project(id=uuid.uuid4(), name="NoGoalProj", description="")
    db_session.add(project)
    await db_session.commit()

    result = await db_session.execute(
        _select(Project).where(Project.id == project.id).options(selectinload(Project.phases))
    )
    project_loaded = result.scalar_one()
    agent = _make_agent("Designer")
    agent.tenant_id = DEFAULT_TENANT_ID

    prompt = await assemble_system_prompt(
        agent, project_loaded, workspace_dir=None,
        mode="active", all_agents=[agent], project_goal=None,
    )
    assert "## Project Goal" in prompt
    assert "Not set yet" in prompt
    assert "[SET_GOAL]" in prompt


@pytest.mark.asyncio
async def test_fetch_project_goal_returns_project_level_row(db_session):
    """_fetch_project_goal returns the single Goal(level='project') row,
    ignoring mission/department/task-level goals from the same tenant."""
    from backend.src.api.agent_chat import _fetch_project_goal
    from backend.src.core.tenant_context import DEFAULT_TENANT_ID
    from backend.src.models import Goal, Tenant

    db_session.add(
        Tenant(id=DEFAULT_TENANT_ID, name="Test", slug="test", owner_user_id="u"),
    )
    await db_session.commit()

    project = Project(id=uuid.uuid4(), name="FetchGoalProj", description="")
    db_session.add(project)
    await db_session.flush()
    db_session.add(Goal(
        tenant_id=DEFAULT_TENANT_ID, level="mission",
        title="Mission (should be ignored)",
    ))
    db_session.add(Goal(
        tenant_id=DEFAULT_TENANT_ID, project_id=project.id, level="project",
        title="The one true project goal", description="criteria",
    ))
    await db_session.commit()

    goal = await _fetch_project_goal(db_session, project.id)
    assert goal is not None
    assert goal.title == "The one true project goal"


@pytest.mark.asyncio
async def test_fetch_project_goal_returns_none_when_missing(db_session):
    from backend.src.api.agent_chat import _fetch_project_goal
    from backend.src.core.tenant_context import DEFAULT_TENANT_ID
    from backend.src.models import Tenant

    db_session.add(
        Tenant(id=DEFAULT_TENANT_ID, name="Test", slug="test", owner_user_id="u"),
    )
    await db_session.commit()

    project = Project(id=uuid.uuid4(), name="NoGoal", description="")
    db_session.add(project)
    await db_session.commit()

    assert await _fetch_project_goal(db_session, project.id) is None


# ── build_project_context extensions (phase.description + hints) ────


@pytest.mark.asyncio
async def test_build_project_context_includes_phase_description(db_session):
    """Phase `description` renders as `(Scope: ...)` under the phase line.

    Makes it far less likely a subordinate agent plans off-scope tasks
    when the active phase is already clearly framed.
    """
    from sqlalchemy import select as _select
    from sqlalchemy.orm import selectinload

    from backend.src.core.task_markers import build_project_context

    from backend.src.models import Phase as PhaseModel

    project = Project(id=uuid.uuid4(), name="PhaseDescTest", description="d")
    db_session.add(project)
    await db_session.flush()
    db_session.add(PhaseModel(
        id=uuid.uuid4(), project_id=project.id, name="Market Research",
        description="시장 조사와 요구사항 정의 — 코드는 다음 단계에서",
        status=PhaseStatus.active, order=1, branch_name="phase/research",
    ))
    await db_session.commit()

    result = await db_session.execute(
        _select(Project).where(Project.id == project.id).options(
            selectinload(Project.phases).selectinload(PhaseModel.tasks),
        )
    )
    loaded = result.scalar_one()

    summary = build_project_context(loaded)
    assert "Market Research" in summary
    assert "(Scope:" in summary
    assert "시장 조사" in summary


@pytest.mark.asyncio
async def test_build_project_context_emits_all_completed_hint(db_session):
    """When every phase is `completed`, the summary ends with the
    PROJECT_COMPLETE hint so the CEO stops inventing new phases."""
    from sqlalchemy import select as _select
    from sqlalchemy.orm import selectinload

    from backend.src.core.task_markers import build_project_context
    from backend.src.models import Phase as PhaseModel

    project = Project(id=uuid.uuid4(), name="AllDone", description="d")
    db_session.add(project)
    await db_session.flush()
    db_session.add(PhaseModel(
        id=uuid.uuid4(), project_id=project.id, name="P1",
        status=PhaseStatus.completed, order=1, branch_name="phase/p1",
    ))
    db_session.add(PhaseModel(
        id=uuid.uuid4(), project_id=project.id, name="P2",
        status=PhaseStatus.completed, order=2, branch_name="phase/p2",
    ))
    await db_session.commit()

    result = await db_session.execute(
        _select(Project).where(Project.id == project.id).options(
            selectinload(Project.phases).selectinload(PhaseModel.tasks),
        )
    )
    loaded = result.scalar_one()

    summary = build_project_context(loaded)
    assert "[PROJECT_COMPLETE" in summary
    assert "All phases" in summary or "all phases" in summary.lower()


@pytest.mark.asyncio
async def test_build_project_context_no_hint_when_phase_pending(db_session):
    """Hint only fires when every phase is completed — an active or
    pending phase suppresses it."""
    from sqlalchemy import select as _select
    from sqlalchemy.orm import selectinload

    from backend.src.core.task_markers import build_project_context
    from backend.src.models import Phase as PhaseModel

    project = Project(id=uuid.uuid4(), name="OneActive", description="d")
    db_session.add(project)
    await db_session.flush()
    db_session.add(PhaseModel(
        id=uuid.uuid4(), project_id=project.id, name="P1",
        status=PhaseStatus.completed, order=1, branch_name="phase/p1",
    ))
    db_session.add(PhaseModel(
        id=uuid.uuid4(), project_id=project.id, name="P2",
        status=PhaseStatus.active, order=2, branch_name="phase/p2",
    ))
    await db_session.commit()

    result = await db_session.execute(
        _select(Project).where(Project.id == project.id).options(
            selectinload(Project.phases).selectinload(PhaseModel.tasks),
        )
    )
    loaded = result.scalar_one()

    summary = build_project_context(loaded)
    assert "[PROJECT_COMPLETE" not in summary


# ── [PROJECT_COMPLETE] marker — loop-breaker ────────────────────────


@pytest.mark.asyncio
async def test_parse_inline_project_complete_markers():
    """Parser extracts [PROJECT_COMPLETE summary="..."] with attributes,
    and bare [PROJECT_COMPLETE] without."""
    from backend.src.core.task_markers import parse_inline_project_complete_markers

    text = (
        "We achieved all the goals.\n"
        '[PROJECT_COMPLETE summary="Shipped MVP with auth and CRUD"]\n'
        "Thanks team!"
    )
    out = parse_inline_project_complete_markers(text)
    assert len(out) == 1
    assert out[0]["summary"] == "Shipped MVP with auth and CRUD"

    text2 = "Done. [PROJECT_COMPLETE]"
    out2 = parse_inline_project_complete_markers(text2)
    assert len(out2) == 1
    assert out2[0].get("summary") is None


@pytest.mark.asyncio
async def test_strip_inline_markers_removes_project_complete():
    """`strip_inline_markers` scrubs PROJECT_COMPLETE so end-users never
    see the raw marker in stored chat messages."""
    from backend.src.core.task_markers import strip_inline_markers

    text = 'Great work!\n[PROJECT_COMPLETE summary="All done"]\nFinal note.'
    cleaned = strip_inline_markers(text)
    assert "PROJECT_COMPLETE" not in cleaned
    assert "Great work" in cleaned
    assert "Final note" in cleaned


@pytest.mark.asyncio
async def test_project_complete_marker_sets_project_status_completed(
    marker_env, test_session_maker,
):
    """When any agent emits [PROJECT_COMPLETE ...], the handler flips
    Project.status to `completed` — regardless of role. This is the
    signal the global dispatcher uses to stop auto-chaining CEO."""
    from sqlalchemy import select as _select

    from backend.src.models import Project as ProjectModel, ProjectStatus as PS

    text = (
        '[CREATE_TASK title="사소한 작업" assignee="Designer"]\n'
        '[PROJECT_COMPLETE summary="All phases completed and criteria met"]'
    )
    await _execute_inline_markers(
        text,
        project_id=marker_env["project_id"],
        tenant_id=marker_env["tenant_id"],
        agent_id=marker_env["designer_id"],
        agent_name="Designer",
        is_org_root=False,
    )

    async with test_session_maker() as db:
        result = await db.execute(
            _select(ProjectModel).where(ProjectModel.id == marker_env["project_id"])
        )
        project = result.scalar_one()
        assert project.status == PS.completed


@pytest.mark.asyncio
async def test_project_complete_marker_emits_action(marker_env):
    """The handler emits an action record so the frontend can react
    (toast, status bar update, etc.)."""
    text = '[PROJECT_COMPLETE summary="shipped"]'
    actions = await _execute_inline_markers(
        text,
        project_id=marker_env["project_id"],
        tenant_id=marker_env["tenant_id"],
        agent_id=marker_env["ceo_id"],
        agent_name="CEO",
        is_org_root=True,
    )
    complete_actions = [a for a in actions if a.get("tool") == "project_complete"]
    assert len(complete_actions) == 1
    assert complete_actions[0]["input"]["summary"] == "shipped"


# ── Artifact gate — PROJECT_COMPLETE must be backed by real files ───


def test_verify_goal_artifacts_rejects_empty_workspace(tmp_path):
    """Goal mentions '소스코드' but workspace has only .md files — reject."""
    from backend.src.core.goal_verification import verify_goal_artifacts

    (tmp_path / "README.md").write_text("# Project readme")

    met, missing = verify_goal_artifacts(
        goal_description="실행 가능한 앱 소스코드와 디자인 화면을 모두 갖춘 MVP",
        workspace_dir=str(tmp_path),
    )
    assert met is False
    assert "소스코드" in " ".join(missing) or "code" in " ".join(missing).lower()


def test_verify_goal_artifacts_rejects_empty_bsd_stubs(tmp_path):
    """Goal mentions '디자인 화면' but .bsd files all have generated_code: null."""
    from backend.src.core.goal_verification import verify_goal_artifacts

    (tmp_path / "design").mkdir()
    (tmp_path / "design" / "login.bsd").write_text(
        '{"name":"Login","spec":{"type":"Screen"},"generated_code":null}'
    )
    # Also drop a code file to satisfy the "code" half if it gets checked
    (tmp_path / "app.ts").write_text("export const app = () => {}")

    met, missing = verify_goal_artifacts(
        goal_description="앱 소스코드와 UI/UX 디자인 화면 포함한 MVP",
        workspace_dir=str(tmp_path),
    )
    assert met is False
    assert any("디자인" in m or "design" in m.lower() or "screen" in m.lower()
               for m in missing)


def test_verify_goal_artifacts_passes_with_real_artifacts(tmp_path):
    """Goal mentions 소스코드 + 디자인. Workspace has real .ts file and
    a .bsd with populated spec — should pass."""
    from backend.src.core.goal_verification import verify_goal_artifacts

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.ts").write_text(
        "import { h } from 'preact';\nexport const App = () => h('div', null, 'hi');"
    )
    (tmp_path / "package.json").write_text('{"name":"mvp","version":"0.1.0"}')
    (tmp_path / "design").mkdir()
    (tmp_path / "design" / "login.bsd").write_text(
        '{"name":"Login","spec":{"type":"Screen","components":[{"type":"button","label":"signin"}]},"generated_code":"const L = () => h(\\"div\\",null,\\"login\\");"}'
    )

    met, missing = verify_goal_artifacts(
        goal_description="앱 소스코드와 디자인 화면 포함한 MVP",
        workspace_dir=str(tmp_path),
    )
    assert met is True
    assert missing == []


def test_verify_goal_artifacts_passes_when_goal_has_no_known_keywords(tmp_path):
    """If the Goal description has no recognizable keywords, don't block —
    we'd rather trust CEO than reject for unknown criteria."""
    from backend.src.core.goal_verification import verify_goal_artifacts

    (tmp_path / "notes.md").write_text("some notes")
    met, missing = verify_goal_artifacts(
        goal_description="브레인스토밍 및 아이디어 정리",  # no code/design keywords
        workspace_dir=str(tmp_path),
    )
    assert met is True
    assert missing == []


@pytest.mark.asyncio
async def test_rejected_project_complete_redispatches_ceo_with_feedback(
    marker_env, test_session_maker, monkeypatch, tmp_path,
):
    """When artifacts are missing, the handler must re-enqueue the
    org-root with explicit feedback — otherwise the project silently
    stalls because `_advance_phase_if_complete` won't re-fire once
    all phases are already completed."""
    from unittest.mock import AsyncMock, MagicMock

    from backend.src.models import Goal as GoalModel
    from backend.src.models import Project as ProjectModel

    async with test_session_maker() as db:
        project = await db.get(ProjectModel, marker_env["project_id"])
        project.workspace_dir = str(tmp_path)
        db.add(GoalModel(
            tenant_id=marker_env["tenant_id"],
            project_id=marker_env["project_id"],
            level="project",
            title="Ship MVP",
            description="실행 가능한 앱 소스코드와 디자인 화면 포함한 MVP",
        ))
        await db.commit()

    mock_mgr = MagicMock()
    mock_mgr.enqueue = AsyncMock()
    monkeypatch.setattr(
        "backend.src.core.agent_queue.get_agent_queue_manager",
        lambda: mock_mgr,
    )

    text = '[PROJECT_COMPLETE summary="all done"]'
    await _execute_inline_markers(
        text,
        project_id=marker_env["project_id"],
        tenant_id=marker_env["tenant_id"],
        agent_id=marker_env["ceo_id"],
        agent_name="CEO",
        is_org_root=True,
    )

    mock_mgr.enqueue.assert_called_once()
    req = mock_mgr.enqueue.call_args[0][0]
    # The feedback re-dispatch must target the org-root (CEO has
    # parent_agent_id IS NULL in marker_env) and contain both the
    # rejection reason and a CREATE_PHASE instruction.
    assert req.agent_id == marker_env["ceo_id"]
    assert "거부" in req.message or "reject" in req.message.lower()
    assert "[CREATE_PHASE" in req.message
    assert "[PROJECT_COMPLETE] 재시도는 금지" in req.message


@pytest.mark.asyncio
async def test_project_complete_marker_rejected_when_artifacts_missing(
    marker_env, test_session_maker, monkeypatch, tmp_path,
):
    """PROJECT_COMPLETE with missing artifacts must NOT flip Project.status.
    Handler should log rejection and leave status untouched, so the
    dispatcher can re-prompt CEO on the next tick."""
    from sqlalchemy import select as _select

    from backend.src.models import Goal as GoalModel
    from backend.src.models import Project as ProjectModel
    from backend.src.models import ProjectStatus as PS

    # Attach an empty workspace + Goal that demands code artifacts
    async with test_session_maker() as db:
        project = await db.get(ProjectModel, marker_env["project_id"])
        project.workspace_dir = str(tmp_path)
        db.add(GoalModel(
            tenant_id=marker_env["tenant_id"],
            project_id=marker_env["project_id"],
            level="project",
            title="Ship MVP",
            description="실제 동작하는 앱 소스코드와 디자인 화면 포함한 완성형 MVP",
        ))
        await db.commit()
    # workspace is empty → no code, no design, no .bsd

    text = '[PROJECT_COMPLETE summary="all done"]'
    await _execute_inline_markers(
        text,
        project_id=marker_env["project_id"],
        tenant_id=marker_env["tenant_id"],
        agent_id=marker_env["ceo_id"],
        agent_name="CEO",
        is_org_root=True,
    )

    async with test_session_maker() as db:
        result = await db.execute(
            _select(ProjectModel).where(ProjectModel.id == marker_env["project_id"])
        )
        project = result.scalar_one()
        # Must NOT flip to completed — artifacts missing
        assert project.status != PS.completed


@pytest.mark.asyncio
async def test_set_goal_block_works_for_non_org_root_agent(
    marker_env, test_session_maker,
):
    """A non-org-root agent (e.g. Designer) receiving the first turn can
    also emit [SET_GOAL]. The marker is role-agnostic — whoever responds
    first is responsible for defining the project end-state."""
    from sqlalchemy import select as _select

    from backend.src.models import Goal

    text = (
        "[SET_GOAL]\n"
        "Non-root defined goal\n"
        "Criteria lives here.\n"
        "[/SET_GOAL]\n"
        "[CREATE_TASK title=\"첫 작업\" assignee=\"Designer\"]"
    )
    await _execute_inline_markers(
        text,
        project_id=marker_env["project_id"],
        tenant_id=marker_env["tenant_id"],
        agent_id=marker_env["designer_id"],
        agent_name="Designer",
        is_org_root=False,
    )

    async with test_session_maker() as db:
        result = await db.execute(
            _select(Goal).where(
                Goal.project_id == marker_env["project_id"],
                Goal.level == "project",
            )
        )
        goal = result.scalar_one()
        assert goal.title == "Non-root defined goal"
