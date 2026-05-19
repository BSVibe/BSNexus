from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from backend.src.core.domain import RequestStatus, WorkPlanCreatedBy
from backend.src.core.orchestration import DECISION_OPTIONS, create_blocking_decision
from backend.src.core.work_steps import WorkStepDraft, create_work_plan
from backend.src.models import Decision, Project, Request, WorkStep


async def _make_request_with_step(db_session, tenant_id: uuid.UUID) -> tuple[Request, WorkStep]:
    project = Project(tenant_id=tenant_id, name="Decision project", description="")
    db_session.add(project)
    await db_session.flush()
    request = Request(tenant_id=tenant_id, project_id=project.id, intent="Build the thing")
    db_session.add(request)
    await db_session.commit()
    await db_session.refresh(request)
    plan = await create_work_plan(
        request=request,
        steps=[WorkStepDraft(name="Implement feature", objective="Do the work")],
        created_by=WorkPlanCreatedBy.system,
        session=db_session,
    )
    step = (await db_session.execute(select(WorkStep).where(WorkStep.plan_id == plan.id))).scalar_one()
    return request, step


@pytest.mark.asyncio
async def test_create_blocking_decision_creates_forward_only_decision(db_session, mock_tenant_id, seeded_tenant):
    request, step = await _make_request_with_step(db_session, mock_tenant_id)

    decision = await create_blocking_decision(
        request=request,
        work_step=step,
        reason="continuation_cap_reached",
        session=db_session,
    )
    await db_session.commit()

    assert decision.blocking is True
    assert decision.options == DECISION_OPTIONS == ["retry", "reframe"]
    assert "abandon" not in decision.options
    assert decision.request_id == request.id
    assert decision.work_step_id == step.id
    assert decision.tenant_id == mock_tenant_id
    assert decision.project_id == request.project_id
    assert step.name in decision.question
    assert "continuation_cap_reached" in decision.question
    assert decision.resolved_at is None

    persisted = (await db_session.execute(select(Decision).where(Decision.id == decision.id))).scalar_one()
    assert persisted.blocking is True


@pytest.mark.asyncio
async def test_create_blocking_decision_tolerates_no_work_step(db_session, mock_tenant_id, seeded_tenant):
    request, _step = await _make_request_with_step(db_session, mock_tenant_id)

    decision = await create_blocking_decision(
        request=request,
        work_step=None,
        reason="orchestration_backstop",
        session=db_session,
    )
    await db_session.commit()

    assert decision.work_step_id is None
    assert decision.blocking is True
    assert decision.options == ["retry", "reframe"]
    assert request.status != RequestStatus.shipped


@pytest.mark.asyncio
async def test_create_blocking_decision_embeds_verifier_detail(db_session, mock_tenant_id, seeded_tenant):
    """B: when the verifier output is supplied, the Decision question
    carries it so the founder sees WHY, not just 'stalled'."""
    request, step = await _make_request_with_step(db_session, mock_tenant_id)

    decision = await create_blocking_decision(
        request=request,
        work_step=step,
        reason="work_step_failed",
        session=db_session,
        detail="[declared_command] failed (exit 137)\n`pnpm build` killed — OOM",
    )
    await db_session.commit()

    assert "What the verifier saw:" in decision.question
    assert "exit 137" in decision.question
    assert "OOM" in decision.question


@pytest.mark.asyncio
async def test_create_blocking_decision_omits_detail_block_when_none(db_session, mock_tenant_id, seeded_tenant):
    request, step = await _make_request_with_step(db_session, mock_tenant_id)

    decision = await create_blocking_decision(
        request=request,
        work_step=step,
        reason="continuation_cap_reached",
        session=db_session,
    )
    await db_session.commit()

    assert "What the verifier saw:" not in decision.question
