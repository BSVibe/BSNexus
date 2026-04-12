"""Tests for the Plan view API: plan-tree, agent-status, SSE events."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient

from backend.src.core.tenant_context import DEFAULT_TENANT_ID
from backend.src.models import (
    Agent,
    Goal,
    Phase,
    PhaseStatus,
    Project,
    ProjectStatus,
    Task,
    TaskPriority,
    TaskSource,
    TaskStatus,
    TaskType,
    Tenant,
)


@pytest.fixture(autouse=True)
async def _seed_default_tenant(db_session):
    """Most plan view fixtures need the default tenant to exist."""
    tenant = Tenant(
        id=DEFAULT_TENANT_ID,
        name="Test Tenant",
        slug="test",
        owner_user_id="test-user",
    )
    db_session.add(tenant)
    await db_session.commit()
    yield


# ── Helpers ──────────────────────────────────────────────────────────


async def _seed_project(db_session, *, name: str = "Plan Project") -> tuple[Project, Phase]:
    now = datetime.now(timezone.utc)
    project = Project(
        id=uuid.uuid4(),
        name=name,
        description="Plan view test project",
        status=ProjectStatus.active,
        created_at=now,
        updated_at=now,
    )
    db_session.add(project)
    await db_session.flush()

    phase = Phase(
        id=uuid.uuid4(),
        project_id=project.id,
        name="Phase 1",
        branch_name="phase-1",
        order=1,
        status=PhaseStatus.active,
        created_at=now,
        updated_at=now,
    )
    db_session.add(phase)
    await db_session.flush()
    return project, phase


async def _add_task(
    db_session,
    project: Project,
    phase: Phase,
    *,
    title: str = "Task",
    status: TaskStatus = TaskStatus.pending,
    agent_id: uuid.UUID | None = None,
) -> Task:
    now = datetime.now(timezone.utc)
    task = Task(
        id=uuid.uuid4(),
        project_id=project.id,
        phase_id=phase.id,
        title=title,
        status=status,
        priority=TaskPriority.medium,
        task_type=TaskType.feature,
        source=TaskSource.llm,
        agent_id=agent_id,
        version=1,
        created_at=now,
        updated_at=now,
    )
    db_session.add(task)
    await db_session.flush()
    return task


async def _add_agent(db_session, *, name: str = "Agent", role: str = "dev") -> Agent:
    now = datetime.now(timezone.utc)
    agent = Agent(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name=name,
        role=role,
        title=role.upper(),
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


# ── plan-tree ────────────────────────────────────────────────────────


async def test_plan_tree_returns_404_for_missing_project(client: AsyncClient) -> None:
    resp = await client.get(f"/api/v1/projects/{uuid.uuid4()}/plan-tree")
    assert resp.status_code == 404


async def test_plan_tree_returns_empty_phases(client: AsyncClient, db_session) -> None:
    now = datetime.now(timezone.utc)
    project = Project(
        id=uuid.uuid4(),
        name="Empty",
        description="",
        status=ProjectStatus.design,
        created_at=now,
        updated_at=now,
    )
    db_session.add(project)
    await db_session.commit()

    resp = await client.get(f"/api/v1/projects/{project.id}/plan-tree")
    assert resp.status_code == 200
    data = resp.json()
    assert data["project_id"] == str(project.id)
    assert data["project_name"] == "Empty"
    assert data["phases"] == []
    assert data["goal"] is None


async def test_plan_tree_groups_tasks_by_phase(client: AsyncClient, db_session) -> None:
    project, phase = await _seed_project(db_session)
    agent = await _add_agent(db_session, name="CTO", role="cto")
    await _add_task(db_session, project, phase, title="Task A")
    await _add_task(db_session, project, phase, title="Task B", status=TaskStatus.running, agent_id=agent.id)
    await db_session.commit()

    resp = await client.get(f"/api/v1/projects/{project.id}/plan-tree")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["phases"]) == 1
    phase_node = data["phases"][0]
    assert phase_node["status"] == "active"
    titles = sorted(t["title"] for t in phase_node["tasks"])
    assert titles == ["Task A", "Task B"]
    running_task = next(t for t in phase_node["tasks"] if t["title"] == "Task B")
    assert running_task["status"] == "running"
    assert running_task["agent_name"] == "CTO"


async def test_plan_tree_orders_phases_by_order_field(client: AsyncClient, db_session) -> None:
    project, phase1 = await _seed_project(db_session)
    now = datetime.now(timezone.utc)
    phase2 = Phase(
        id=uuid.uuid4(),
        project_id=project.id,
        name="Phase 2",
        branch_name="phase-2",
        order=2,
        status=PhaseStatus.pending,
        created_at=now,
        updated_at=now,
    )
    db_session.add(phase2)
    await db_session.commit()

    resp = await client.get(f"/api/v1/projects/{project.id}/plan-tree")
    data = resp.json()
    assert [p["name"] for p in data["phases"]] == ["Phase 1", "Phase 2"]
    assert [p["order"] for p in data["phases"]] == [1, 2]


async def test_plan_tree_includes_goal_when_present(client: AsyncClient, db_session) -> None:
    project, _ = await _seed_project(db_session)
    db_session.add(
        Goal(
            id=uuid.uuid4(),
            tenant_id=DEFAULT_TENANT_ID,
            level="project",
            title="Ship MVP by Q3",
            project_id=project.id,
            parent_goal_id=None,
        )
    )
    await db_session.commit()

    resp = await client.get(f"/api/v1/projects/{project.id}/plan-tree")
    assert resp.json()["goal"] == "Ship MVP by Q3"


# ── agent-status ─────────────────────────────────────────────────────


async def test_agent_status_returns_404_for_missing_project(client: AsyncClient) -> None:
    resp = await client.get(f"/api/v1/projects/{uuid.uuid4()}/agent-status")
    assert resp.status_code == 404


async def test_agent_status_marks_running_agent_green(client: AsyncClient, db_session) -> None:
    project, phase = await _seed_project(db_session)
    agent = await _add_agent(db_session, name="QA", role="qa")
    await _add_task(db_session, project, phase, title="Run tests", status=TaskStatus.running, agent_id=agent.id)
    await db_session.commit()

    resp = await client.get(f"/api/v1/projects/{project.id}/agent-status")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    card = data[0]
    assert card["agent_id"] == str(agent.id)
    assert card["dot"] == "green"
    assert card["current_task"]["title"] == "Run tests"


async def test_agent_status_marks_idle_online_agent_yellow(client: AsyncClient, db_session) -> None:
    project, _ = await _seed_project(db_session)
    await _add_agent(db_session, name="CMO", role="cmo")
    await db_session.commit()

    resp = await client.get(f"/api/v1/projects/{project.id}/agent-status")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["dot"] == "yellow"
    assert data[0]["current_task"] is None


async def test_agent_status_marks_offline_agent_gray(client: AsyncClient, db_session) -> None:
    project, _ = await _seed_project(db_session)
    agent = await _add_agent(db_session, name="Dev", role="dev")
    agent.status = "offline"
    await db_session.commit()

    resp = await client.get(f"/api/v1/projects/{project.id}/agent-status")
    data = resp.json()
    assert data[0]["dot"] == "gray"


async def test_agent_status_excludes_inactive_agents(client: AsyncClient, db_session) -> None:
    project, _ = await _seed_project(db_session)
    agent = await _add_agent(db_session, name="Retired", role="retired")
    agent.is_active = False
    await db_session.commit()

    resp = await client.get(f"/api/v1/projects/{project.id}/agent-status")
    assert resp.json() == []


# ── Direct-call tests for full coverage ─────────────────────────────


@pytest.mark.asyncio
async def test_get_plan_tree_direct_with_full_population(db_session):
    """Walk every branch in get_plan_tree without going through ASGI."""
    from backend.src.api.plan_tree import get_plan_tree
    from backend.src.core.tenant_context import DEFAULT_TENANT_ID
    from backend.src.models import Goal

    project, phase = await _seed_project(db_session, name="Full Plan")
    agent = await _add_agent(db_session, name="DEV", role="dev")
    await _add_task(db_session, project, phase, title="Task A", agent_id=agent.id)
    await _add_task(db_session, project, phase, title="Task B", status=TaskStatus.running)

    db_session.add(
        Goal(
            id=uuid.uuid4(),
            tenant_id=DEFAULT_TENANT_ID,
            level="project",
            title="Ship Q3",
            project_id=project.id,
            parent_goal_id=None,
        )
    )
    await db_session.commit()

    tree = await get_plan_tree(project_id=project.id, db=db_session)
    assert tree.project_name == "Full Plan"
    assert tree.goal == "Ship Q3"
    assert len(tree.phases) == 1
    titles = sorted(t.title for t in tree.phases[0].tasks)
    assert titles == ["Task A", "Task B"]
    # Agent name resolved on the agent-attached task.
    task_with_agent = next(t for t in tree.phases[0].tasks if t.title == "Task A")
    assert task_with_agent.agent_name == "DEV"


@pytest.mark.asyncio
async def test_get_plan_tree_direct_404(db_session):
    from fastapi import HTTPException

    from backend.src.api.plan_tree import get_plan_tree

    with pytest.raises(HTTPException) as exc:
        await get_plan_tree(project_id=uuid.uuid4(), db=db_session)
    assert exc.value.status_code == 404


def _fake_request(redis: object | None = None) -> object:
    """Build a minimal Request stand-in with ``app.state.redis``."""
    from types import SimpleNamespace

    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=redis)))


@pytest.mark.asyncio
async def test_get_agent_status_direct_all_dot_colors(db_session):
    """Cover the resolve_agent_status_dot branches end-to-end."""
    from backend.src.api.plan_tree import get_agent_status
    from backend.src.core.tenant_context import DEFAULT_TENANT_ID

    project, phase = await _seed_project(db_session)
    running_agent = await _add_agent(db_session, name="Worker A", role="worker")
    _idle_agent = await _add_agent(db_session, name="Worker B", role="worker")
    offline_agent = await _add_agent(db_session, name="Worker C", role="worker")
    # Worker C is also detached from any worker pool, so the worker-type
    # online fallback should not promote it back to yellow.
    offline_agent.status = "offline"
    offline_agent.executor_type = "generic_llm"

    await _add_task(
        db_session, project, phase, title="Run me", status=TaskStatus.running, agent_id=running_agent.id,
    )
    await db_session.commit()

    cards = await get_agent_status(
        project_id=project.id,
        request=_fake_request(),
        db=db_session,
        tenant_id=DEFAULT_TENANT_ID,
    )
    by_name = {c.name: c for c in cards}
    assert by_name["Worker A"].dot == "green"
    assert by_name["Worker A"].current_task is not None
    assert by_name["Worker B"].dot == "yellow"
    assert by_name["Worker C"].dot == "gray"


@pytest.mark.asyncio
async def test_get_agent_status_direct_404(db_session):
    from fastapi import HTTPException

    from backend.src.api.plan_tree import get_agent_status
    from backend.src.core.tenant_context import DEFAULT_TENANT_ID

    with pytest.raises(HTTPException) as exc:
        await get_agent_status(
            project_id=uuid.uuid4(),
            request=_fake_request(),
            db=db_session,
            tenant_id=DEFAULT_TENANT_ID,
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_status_dot_helper_blocked_branch(db_session):
    """Direct call to _status_dot for the blocked code path."""
    from backend.src.api.plan_tree import _status_dot
    from backend.src.models import Task

    blocked_task = Task(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        phase_id=uuid.uuid4(),
        title="X",
        status=TaskStatus.blocked,
        priority=TaskPriority.medium,
        task_type=TaskType.feature,
        source=TaskSource.llm,
        version=1,
    )
    agent = await _add_agent(db_session, name="W", role="w")
    await db_session.commit()
    assert _status_dot(agent, blocked_task) == "red"
