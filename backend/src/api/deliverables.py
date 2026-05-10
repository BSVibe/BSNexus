from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request as HttpRequest, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.auth import get_current_user
from backend.src.core.deliverables import WorkOutputDraft, create_deliverable_from_work_output
from backend.src.core.domain import DeliverableStatus, ProofAttemptStatus, ProofState
from backend.src.core.tenant_context import get_tenant_id
from backend.src.models import Deliverable, Project, ProofAttempt, Request, WorkStep
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


@router.post("", response_model=DeliverableResponse, status_code=status.HTTP_201_CREATED)
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


@router.get("", response_model=list[DeliverableResponse])
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


@router.post("/{deliverable_id}/verify", response_model=DeliverableResponse)
async def verify_deliverable(
    deliverable_id: uuid.UUID,
    request: HttpRequest,
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Manually re-enqueue the Verifier Worker for this deliverable
    (decision-locks **A1**).

    Today this stamps ``proof_state=verifying`` and records a fresh
    ProofAttempt(running) so the founder gets immediate feedback (the
    DeliverableCard ``ProofBadge`` flips to verifying). The
    deterministic worker that completes the attempt is wired during
    the quality-engineering phase; until then the queue entry sits in
    ``running`` for the worker to pick up.

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
    attempt = ProofAttempt(
        deliverable_id=deliverable.id,
        # The matched policy's verifier_type overwrites this when the
        # worker picks the attempt up; until then ``re_verify_pending``
        # records the trigger provenance.
        verifier_type="re_verify_pending",
        inputs={"trigger": "manual_re_verify"},
        status=ProofAttemptStatus.running,
    )
    db.add(attempt)
    await db.commit()
    await db.refresh(deliverable)
    await db.refresh(attempt)

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
            "attempt_id": str(attempt.id),
        },
    )
    return await _deliverable_response(db, deliverable)


async def _deliverable_response(db: AsyncSession, deliverable: Deliverable) -> dict:
    latest_attempt = (
        await db.execute(
            select(ProofAttempt)
            .where(ProofAttempt.deliverable_id == deliverable.id)
            .order_by(ProofAttempt.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
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
        "proof_policy_id": deliverable.proof_policy_id,
        "proof_status": {
            "state": deliverable.proof_state,
            "policy_id": deliverable.proof_policy_id,
            "latest_attempt_id": latest_attempt.id if latest_attempt is not None else None,
            "latest_attempt_status": latest_attempt.status if latest_attempt is not None else None,
            "latest_attempt_summary": latest_attempt.proof_summary if latest_attempt is not None else None,
            "latest_attempt_completed_at": latest_attempt.completed_at if latest_attempt is not None else None,
        },
        "status": deliverable.status,
        "risk_summary": deliverable.risk_summary,
        "created_at": deliverable.created_at,
        "updated_at": deliverable.updated_at,
    }
