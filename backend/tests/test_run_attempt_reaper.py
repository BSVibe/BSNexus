"""G-A — startup reaper for orphaned RunAttempts.

A work phase runs in-process. If the process dies mid-phase (container
recreate, crash) the RunAttempt is never finished and sits ``running``
forever — a zombie no founder Decision can reach. ``reap_orphaned_run_
attempts`` runs once at startup: every ``running`` RunAttempt is by
definition orphaned (its owning process is gone), so it is failed, its
WorkStep failed, and the Request routed to ``needs_decision`` with a
founder Decision.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from backend.src.core.domain import (
    RunAttemptStatus,
    RequestStatus,
    WorkPlanCreatedBy,
    WorkStepStatus,
)
from backend.src.core.orchestration import reap_orphaned_run_attempts
from backend.src.core.run_attempts import create_run_attempt, finish_run_attempt
from backend.src.core.work_steps import WorkStepDraft, create_work_plan, transition_work_step
from backend.src.models import Decision, Project, Request, WorkStep


async def _seed_running_attempt(db_session, tenant_id: uuid.UUID):
    """Seed Request → WorkPlan → WorkStep(running) → RunAttempt(running)."""
    project = Project(tenant_id=tenant_id, name="reaper", description="")
    db_session.add(project)
    await db_session.flush()
    request = Request(tenant_id=tenant_id, project_id=project.id, intent="zombie test")
    db_session.add(request)
    await db_session.commit()
    await db_session.refresh(request)

    plan = await create_work_plan(
        request=request,
        steps=[WorkStepDraft(name="impl", objective="do it")],
        created_by=WorkPlanCreatedBy.system,
        session=db_session,
    )
    step = (await db_session.execute(select(WorkStep).where(WorkStep.plan_id == plan.id))).scalar_one()
    await transition_work_step(step=step, target=WorkStepStatus.running, session=db_session)
    attempt = await create_run_attempt(work_step=step, executor_kind="injected", model="stub", session=db_session)
    return request, step, attempt


@pytest.mark.asyncio
async def test_reaper_fails_orphaned_attempt_and_raises_decision(db_session, mock_tenant_id, seeded_tenant):
    request, step, attempt = await _seed_running_attempt(db_session, mock_tenant_id)
    assert attempt.status == RunAttemptStatus.running

    count = await reap_orphaned_run_attempts(session=db_session, stream_manager=None)
    assert count == 1

    await db_session.refresh(attempt)
    await db_session.refresh(step)
    await db_session.refresh(request)
    assert attempt.status == RunAttemptStatus.failed
    assert attempt.terminal_reason == "process_lost"
    assert step.status == WorkStepStatus.failed
    assert request.status == RequestStatus.needs_decision

    decisions = (await db_session.execute(select(Decision).where(Decision.request_id == request.id))).scalars().all()
    assert len(decisions) == 1
    assert decisions[0].resolved_at is None
    assert decisions[0].work_step_id == step.id


@pytest.mark.asyncio
async def test_reaper_leaves_completed_attempt_untouched(db_session, mock_tenant_id, seeded_tenant):
    _request, _step, attempt = await _seed_running_attempt(db_session, mock_tenant_id)
    await finish_run_attempt(
        attempt=attempt,
        status=RunAttemptStatus.completed,
        terminal_reason="summarized",
        session=db_session,
    )

    count = await reap_orphaned_run_attempts(session=db_session, stream_manager=None)
    assert count == 0
    await db_session.refresh(attempt)
    assert attempt.status == RunAttemptStatus.completed


@pytest.mark.asyncio
async def test_reaper_no_op_when_nothing_running(db_session, mock_tenant_id, seeded_tenant):
    count = await reap_orphaned_run_attempts(session=db_session, stream_manager=None)
    assert count == 0
