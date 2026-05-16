from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request as HttpRequest, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.auth import get_current_user, require_permission
from backend.src.core.deliverables import WorkOutputDraft, create_deliverable_from_work_output
from backend.src.core.domain import (
    DeliverableStatus,
    ProofAspectType,
    ProofState,
)
from backend.src.core.git_ops import build_deliverable_diff_url
from backend.src.core.tenant_context import get_tenant_id
from backend.src.models import Deliverable, Project, Request, VerificationAspect, WorkStep
from backend.src.queue.streams import RedisStreamManager
from backend.src.schemas import DeliverableCreate, DeliverableResponse
from backend.src.storage.database import get_db

router = APIRouter(prefix="/api/v1/deliverables", tags=["deliverables"])

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


async def _assert_project_belongs(db: AsyncSession, project_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
    stmt = select(Project.id).where(Project.id == project_id, Project.tenant_id == tenant_id)
    if (await db.execute(stmt)).scalar_one_or_none() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")


async def _assert_request_belongs(
    db: AsyncSession,
    request_id: uuid.UUID,
    project_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> None:
    stmt = select(Request.id).where(
        Request.id == request_id,
        Request.project_id == project_id,
        Request.tenant_id == tenant_id,
    )
    if (await db.execute(stmt)).scalar_one_or_none() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Request not found")


async def _assert_work_step_belongs(
    db: AsyncSession,
    work_step_id: uuid.UUID,
    project_id: uuid.UUID,
    request_id: uuid.UUID | None,
    tenant_id: uuid.UUID,
) -> None:
    stmt = (
        select(WorkStep.id)
        .join(Request, Request.id == WorkStep.request_id)
        .where(
            WorkStep.id == work_step_id,
            Request.tenant_id == tenant_id,
            Request.project_id == project_id,
        )
    )
    if request_id is not None:
        stmt = stmt.where(WorkStep.request_id == request_id)
    if (await db.execute(stmt)).scalar_one_or_none() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "WorkStep not found")


@router.post(
    "",
    response_model=DeliverableResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("bsnexus.deliverables.write"))],
)
async def create_deliverable(
    payload: DeliverableCreate,
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await _assert_project_belongs(db, payload.project_id, tenant_id)
    if payload.request_id is not None:
        await _assert_request_belongs(db, payload.request_id, payload.project_id, tenant_id)
    if payload.work_step_id is not None:
        await _assert_work_step_belongs(db, payload.work_step_id, payload.project_id, payload.request_id, tenant_id)

    deliverable = await create_deliverable_from_work_output(
        tenant_id=tenant_id,
        draft=WorkOutputDraft(
            project_id=payload.project_id,
            request_id=payload.request_id,
            work_step_id=payload.work_step_id,
            type=payload.type,
            title=payload.title,
            summary=payload.summary,
            artifact_refs=payload.artifact_refs,
            risk_summary=payload.risk_summary,
        ),
        session=db,
    )
    return await _deliverable_response(db, deliverable)


@router.get(
    "",
    response_model=list[DeliverableResponse],
    dependencies=[Depends(require_permission("bsnexus.deliverables.read"))],
)
async def list_deliverables(
    project_id: uuid.UUID | None = Query(None),
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> list[Deliverable]:
    if project_id is not None:
        await _assert_project_belongs(db, project_id, tenant_id)

    stmt = select(Deliverable).where(Deliverable.tenant_id == tenant_id)
    if project_id is not None:
        stmt = stmt.where(Deliverable.project_id == project_id)
    stmt = stmt.order_by(Deliverable.created_at.desc()).limit(limit)
    deliverables = list((await db.execute(stmt)).scalars())
    return [await _deliverable_response(db, deliverable) for deliverable in deliverables]


@router.post(
    "/{deliverable_id}/verify",
    response_model=DeliverableResponse,
    dependencies=[Depends(require_permission("bsnexus.deliverables.write"))],
)
async def verify_deliverable(
    deliverable_id: uuid.UUID,
    request: HttpRequest,
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Manually re-enqueue the Verifier Worker for this deliverable
    (decision-locks **A1**).

    Stamps ``proof_state=verifying`` so the founder gets immediate
    feedback (the DeliverableCard ``ProofBadge`` flips). The worker
    consumes ``proof:queue`` and overwrites the deliverable's aspect
    rows on its next pass — we deliberately do NOT pre-seed a
    ``re_verify_pending`` aspect row, since the new multi-aspect model
    creates the right aspects (test/lint/install_smoke) from the
    workspace at run time.

    Tenant-scoped: a deliverable that belongs to another tenant 404s
    so verifier capacity can't be burned across tenant boundaries.
    """
    stmt = select(Deliverable).where(
        Deliverable.id == deliverable_id,
        Deliverable.tenant_id == tenant_id,
    )
    deliverable = (await db.execute(stmt)).scalar_one_or_none()
    if deliverable is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Deliverable not found")

    deliverable.proof_state = ProofState.verifying
    deliverable.status = DeliverableStatus.verifying
    await db.commit()
    await db.refresh(deliverable)

    # G7.2 SSE wiring — fan the proof-state transition onto the project
    # stream so other open BSNexus tabs flip the badge without a manual
    # refresh. ``useProjectEvents.deliverable_proof`` invalidates
    # ``['deliverables', projectId]`` and ``['brief', projectId]``.
    stream_manager: RedisStreamManager = request.app.state.stream_manager
    await stream_manager.publish_project_event(
        str(deliverable.project_id),
        "deliverable_proof",
        {
            "id": str(deliverable.id),
            "project_id": str(deliverable.project_id),
            "proof_state": deliverable.proof_state.value,
        },
    )
    # G6.1 VerifierWorker handoff — the route only stamps the running
    # state; the worker consumes ``proof:queue`` and finishes the
    # deliverable's proof_state (verified / verification_failed /
    # human_review_required). Tenant-scoped payload so the consumer
    # can refuse cross-tenant pulls.
    from backend.src.workers.verifier import PROOF_QUEUE_STREAM  # noqa: PLC0415

    await stream_manager.publish(
        PROOF_QUEUE_STREAM,
        {
            "deliverable_id": str(deliverable.id),
            "tenant_id": str(tenant_id),
        },
    )
    return await _deliverable_response(db, deliverable)


async def _deliverable_response(db: AsyncSession, deliverable: Deliverable) -> dict:
    aspect_rows = (
        (
            await db.execute(
                select(VerificationAspect)
                .where(VerificationAspect.deliverable_id == deliverable.id)
                .order_by(VerificationAspect.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    latest_test = next(
        (a for a in reversed(aspect_rows) if a.aspect_type == ProofAspectType.code_test),
        None,
    )
    project = await db.get(Project, deliverable.project_id)
    diff_url = build_deliverable_diff_url(project=project, deliverable=deliverable) if project else None
    return {
        "id": deliverable.id,
        "tenant_id": deliverable.tenant_id,
        "project_id": deliverable.project_id,
        "request_id": deliverable.request_id,
        "work_step_id": deliverable.work_step_id,
        "type": deliverable.type,
        "title": deliverable.title,
        "summary": deliverable.summary,
        "artifact_refs": deliverable.artifact_refs,
        "proof_state": deliverable.proof_state,
        "proof_status": {
            "state": deliverable.proof_state,
            "aspects": [
                {
                    "id": a.id,
                    "aspect_type": a.aspect_type,
                    "status": a.status,
                    "exit_code": a.exit_code,
                    "summary": a.result_summary,
                    "completed_at": a.completed_at,
                    "blocking": a.blocking,
                }
                for a in aspect_rows
            ],
            "latest_test_status": latest_test.status if latest_test is not None else None,
            "latest_test_summary": latest_test.result_summary if latest_test is not None else None,
            "latest_test_completed_at": latest_test.completed_at if latest_test is not None else None,
        },
        "status": deliverable.status,
        "risk_summary": deliverable.risk_summary,
        "commit_sha": deliverable.commit_sha,
        "diff_url": diff_url,
        "created_at": deliverable.created_at,
        "updated_at": deliverable.updated_at,
    }
