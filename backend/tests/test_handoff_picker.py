"""Tests for _pick_next_handoff_agent — the helper that chooses who to @mention
when a passive agent forgets to hand off at the end of a task."""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from backend.src.api.agent_chat import _pick_next_handoff_agent
from backend.src.models import (
    Agent,
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

_TENANT = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _wrap_session(session):
    """Mimic ``async with async_session() as db:`` for tests."""
    class _Wrap:
        def __init__(self, sess):
            self._sess = sess
        async def __aenter__(self):
            return self._sess
        async def __aexit__(self, exc_type, exc, tb):
            return False
    return lambda: _Wrap(session)


@pytest_asyncio.fixture
async def seeded(db_session, monkeypatch):
    monkeypatch.setattr(
        "backend.src.api.agent_chat.async_session", _wrap_session(db_session)
    )
    db_session.add(Tenant(id=_TENANT, name="T", slug="t", owner_user_id="u"))
    project = Project(id=uuid.uuid4(), name="P", description="d", status=ProjectStatus.active, repo_path="/tmp/p")
    db_session.add(project)
    await db_session.flush()

    phase1 = Phase(id=uuid.uuid4(), project_id=project.id, name="Planning", order=1, status=PhaseStatus.active, branch_name="main")
    db_session.add(phase1)
    await db_session.flush()

    ceo = Agent(id=uuid.uuid4(), tenant_id=_TENANT, name="CEO", role="ceo", title="CEO",
                job_description="j", executor_type="generic_llm", executor_config={}, capabilities=[], is_active=True)
    cpo = Agent(id=uuid.uuid4(), tenant_id=_TENANT, name="CPO", role="cpo", title="CPO",
                job_description="j", executor_type="generic_llm", executor_config={}, capabilities=[], is_active=True, parent_agent_id=ceo.id)
    designer = Agent(id=uuid.uuid4(), tenant_id=_TENANT, name="Designer", role="designer", title="D",
                     job_description="j", executor_type="generic_llm", executor_config={}, capabilities=[], is_active=True, parent_agent_id=ceo.id)
    db_session.add_all([ceo, cpo, designer])
    await db_session.flush()

    def _mk_task(title, assigned, status=TaskStatus.pending):
        return Task(
            id=uuid.uuid4(), project_id=project.id, phase_id=phase1.id,
            title=title, status=status, priority=TaskPriority.medium,
            task_type=TaskType.feature, source=TaskSource.llm,
            assigned_agent_id=assigned.id,
        )

    done_task = _mk_task("Research", cpo, TaskStatus.done)
    pending_design = _mk_task("Wireframes", designer, TaskStatus.pending)
    db_session.add_all([done_task, pending_design])
    await db_session.commit()

    return {
        "project": project,
        "phase": phase1,
        "ceo": ceo,
        "cpo": cpo,
        "designer": designer,
        "done_task": done_task,
        "pending": pending_design,
    }


@pytest.mark.asyncio
async def test_picks_same_phase_pending_assignee(seeded):
    s = seeded
    all_agents = [s["ceo"], s["cpo"], s["designer"]]
    picked = await _pick_next_handoff_agent(
        project_id=s["project"].id,
        current_agent_id=s["cpo"].id,
        all_agents=all_agents,
        completed_task_id=s["done_task"].id,
    )
    assert picked is not None
    # Designer has the next pending task in the same phase
    assert picked.name == "Designer"


@pytest.mark.asyncio
async def test_falls_back_to_org_root_when_nothing_pending(seeded, db_session):
    from sqlalchemy import update
    s = seeded
    # Mark all pending tasks done — no follow-up assignees
    await db_session.execute(
        update(Task).where(Task.id == s["pending"].id).values(status=TaskStatus.done)
    )
    await db_session.commit()

    picked = await _pick_next_handoff_agent(
        project_id=s["project"].id,
        current_agent_id=s["cpo"].id,
        all_agents=[s["ceo"], s["cpo"], s["designer"]],
        completed_task_id=s["done_task"].id,
    )
    # Falls back to CEO (org root)
    assert picked is not None
    assert picked.name == "CEO"


@pytest.mark.asyncio
async def test_does_not_handoff_to_self(seeded, db_session):
    from sqlalchemy import update
    s = seeded
    # Reassign the only pending task to CPO — CPO must not handoff to itself
    await db_session.execute(
        update(Task).where(Task.id == s["pending"].id).values(assigned_agent_id=s["cpo"].id)
    )
    await db_session.commit()

    picked = await _pick_next_handoff_agent(
        project_id=s["project"].id,
        current_agent_id=s["cpo"].id,
        all_agents=[s["ceo"], s["cpo"], s["designer"]],
        completed_task_id=s["done_task"].id,
    )
    # Should skip CPO's own pending task and go to org root instead
    assert picked is not None
    assert picked.name != "CPO"
