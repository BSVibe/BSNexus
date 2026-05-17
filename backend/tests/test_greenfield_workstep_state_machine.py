from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from backend.src.core.domain import (
    DeliverableStatus,
    DeliverableType,
    ProofState,
    RequestStatus,
    WorkPlanCreatedBy,
    WorkPlanStatus,
    WorkStepStatus,
)
from backend.src.core.work_steps import (
    GreenfieldStateError,
    WorkStepDraft,
    create_work_plan,
    transition_request,
    transition_work_step,
)
from backend.src.models import Deliverable, Project, Request, Tenant, WorkPlan, WorkStep


async def _make_request(db_session, tenant_id: uuid.UUID) -> Request:
    project = Project(tenant_id=tenant_id, name="Greenfield G2", description="")
    db_session.add(project)
    await db_session.flush()
    request = Request(
        tenant_id=tenant_id,
        project_id=project.id,
        intent="Create a proof-gated work plan",
    )
    db_session.add(request)
    await db_session.commit()
    await db_session.refresh(request)
    return request


@pytest.mark.asyncio
async def test_create_work_plan_creates_steps_and_moves_request_to_running(db_session, mock_tenant_id, seeded_tenant):
    request = await _make_request(db_session, mock_tenant_id)

    plan = await create_work_plan(
        request=request,
        steps=[
            WorkStepDraft(
                name="Implement state gates",
                objective="Add backend-owned request and step transitions",
                expected_outputs=["transition tests"],
                verifier_policy={"kind": "test"},
            ),
            WorkStepDraft(
                name="Review proof gate",
                objective="Confirm shipped requires verified deliverables",
                expected_outputs=["proof gate test"],
            ),
        ],
        created_by=WorkPlanCreatedBy.system,
        session=db_session,
    )

    await db_session.refresh(request)
    steps = (
        (await db_session.execute(select(WorkStep).where(WorkStep.request_id == request.id).order_by(WorkStep.name)))
        .scalars()
        .all()
    )

    assert plan.status == WorkPlanStatus.active
    assert plan.version == 1
    assert len(plan.steps) == 2
    assert request.status == RequestStatus.running
    assert request.current_step_id is not None
    assert len(steps) == 2


@pytest.mark.asyncio
async def test_new_active_work_plan_supersedes_previous_plan(db_session, mock_tenant_id, seeded_tenant):
    request = await _make_request(db_session, mock_tenant_id)
    first_plan = await create_work_plan(
        request=request,
        steps=[WorkStepDraft(name="First", objective="First objective")],
        created_by=WorkPlanCreatedBy.system,
        session=db_session,
    )

    second_plan = await create_work_plan(
        request=request,
        steps=[WorkStepDraft(name="Second", objective="Second objective")],
        created_by=WorkPlanCreatedBy.user,
        session=db_session,
    )

    refreshed_first = await db_session.get(WorkPlan, first_plan.id)
    assert refreshed_first.status == WorkPlanStatus.superseded
    assert second_plan.status == WorkPlanStatus.active
    assert second_plan.version == 2


@pytest.mark.asyncio
async def test_work_step_cannot_skip_state_transitions(db_session, mock_tenant_id, seeded_tenant):
    request = await _make_request(db_session, mock_tenant_id)
    plan = await create_work_plan(
        request=request,
        steps=[WorkStepDraft(name="Step", objective="Objective")],
        created_by=WorkPlanCreatedBy.system,
        session=db_session,
    )
    work_step = (await db_session.execute(select(WorkStep).where(WorkStep.plan_id == plan.id))).scalar_one()

    with pytest.raises(GreenfieldStateError):
        await transition_work_step(step=work_step, target=WorkStepStatus.review_ready, session=db_session)

    await transition_work_step(step=work_step, target=WorkStepStatus.running, session=db_session)
    assert work_step.status == WorkStepStatus.running
    assert work_step.attempt_count == 1

    await transition_work_step(step=work_step, target=WorkStepStatus.verifying, session=db_session)
    await transition_work_step(step=work_step, target=WorkStepStatus.review_ready, session=db_session)
    assert work_step.status == WorkStepStatus.review_ready


@pytest.mark.asyncio
async def test_request_needs_decision_resume_review_ready_path_is_server_gated(
    db_session, mock_tenant_id, seeded_tenant
):
    request = await _make_request(db_session, mock_tenant_id)

    with pytest.raises(GreenfieldStateError):
        await transition_request(request=request, target=RequestStatus.review_ready, session=db_session)

    await transition_request(request=request, target=RequestStatus.running, session=db_session)
    await transition_request(request=request, target=RequestStatus.needs_decision, session=db_session)
    assert request.status == RequestStatus.needs_decision

    await transition_request(request=request, target=RequestStatus.running, session=db_session)
    await transition_request(request=request, target=RequestStatus.review_ready, session=db_session)
    assert request.status == RequestStatus.review_ready


@pytest.mark.asyncio
async def test_request_running_to_blocked_is_no_longer_valid(db_session, mock_tenant_id, seeded_tenant):
    from backend.src.core.state_machines import REQUEST_TRANSITIONS, can_transition_request

    request = await _make_request(db_session, mock_tenant_id)
    await transition_request(request=request, target=RequestStatus.running, session=db_session)

    assert RequestStatus.needs_decision in REQUEST_TRANSITIONS[RequestStatus.running]
    assert can_transition_request(RequestStatus.needs_decision, RequestStatus.running)
    # 'blocked' is retired — the enum no longer carries it.
    assert not hasattr(RequestStatus, "blocked")


@pytest.mark.asyncio
async def test_request_cannot_ship_without_verified_deliverable_proof(db_session, mock_tenant_id, seeded_tenant):
    request = await _make_request(db_session, mock_tenant_id)
    await transition_request(request=request, target=RequestStatus.running, session=db_session)
    await transition_request(request=request, target=RequestStatus.review_ready, session=db_session)

    with pytest.raises(GreenfieldStateError):
        await transition_request(request=request, target=RequestStatus.shipped, session=db_session)

    deliverable = Deliverable(
        tenant_id=request.tenant_id,
        project_id=request.project_id,
        request_id=request.id,
        type=DeliverableType.code,
        title="Implementation",
        artifact_refs=["git:abc123"],
        status=DeliverableStatus.review_ready,
        proof_state=ProofState.verification_missing,
    )
    db_session.add(deliverable)
    await db_session.commit()

    with pytest.raises(GreenfieldStateError):
        await transition_request(request=request, target=RequestStatus.shipped, session=db_session)

    deliverable.proof_state = ProofState.verified
    deliverable.status = DeliverableStatus.shipped
    await db_session.commit()

    await transition_request(request=request, target=RequestStatus.shipped, session=db_session)
    assert request.status == RequestStatus.shipped


@pytest.mark.asyncio
async def test_request_ship_gate_is_tenant_scoped_by_request_identity(db_session, mock_tenant_id, seeded_tenant):
    request = await _make_request(db_session, mock_tenant_id)
    other_tenant_id = uuid.uuid4()
    other_tenant = Tenant(
        id=other_tenant_id,
        name="Other",
        slug=f"other-{other_tenant_id.hex[:8]}",
        owner_user_id="other",
    )
    db_session.add(other_tenant)
    await db_session.commit()

    other_project = Project(tenant_id=other_tenant_id, name="Other", description="")
    db_session.add(other_project)
    await db_session.flush()
    db_session.add(
        Deliverable(
            tenant_id=other_tenant_id,
            project_id=other_project.id,
            request_id=None,
            type=DeliverableType.code,
            title="Other proof",
            artifact_refs=["git:other"],
            status=DeliverableStatus.shipped,
            proof_state=ProofState.verified,
        )
    )
    await db_session.commit()

    await transition_request(request=request, target=RequestStatus.running, session=db_session)
    await transition_request(request=request, target=RequestStatus.review_ready, session=db_session)

    with pytest.raises(GreenfieldStateError):
        await transition_request(request=request, target=RequestStatus.shipped, session=db_session)
