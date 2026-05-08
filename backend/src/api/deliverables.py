"""Deliverables list + manual proof re-verification.

Resource shape: decision-locks A3 (flat REST). Proof model: decision-locks
A1 (verifier worker enqueues from a flat queue keyed by ``verifier_type``).

- ``GET  /api/v1/deliverables?project_id={id}``  — scoped to a project
- ``GET  /api/v1/deliverables``                  — cross-project (tenant-scoped)
- ``POST /api/v1/deliverables/{id}/verify``      — manual re-enqueue
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.auth import get_current_user
from backend.src.core.tenant_context import get_tenant_id
from backend.src.core.verifier.enqueue import enqueue_verification
from backend.src.core.verifier.protocol import VerificationEnvelope, VerifierType
from backend.src.models import Deliverable, Project
from backend.src.schemas import DeliverableResponse
from backend.src.storage.database import get_db

router = APIRouter(prefix="/api/v1/deliverables", tags=["deliverables"])

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


async def _assert_project_belongs(db: AsyncSession, project_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
    stmt = select(Project.id).where(Project.id == project_id, Project.tenant_id == tenant_id)
    if (await db.execute(stmt)).scalar_one_or_none() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")


@router.get(
    "",
    response_model=list[DeliverableResponse],
)
async def list_deliverables(
    project_id: uuid.UUID | None = Query(
        None, description="Filter to a single project. Omit for tenant-wide cross-project list."
    ),
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
    return list((await db.execute(stmt)).scalars())


@router.post(
    "/{deliverable_id}/verify",
    response_model=DeliverableResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def verify_deliverable(
    deliverable_id: uuid.UUID,
    request: Request,
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> Deliverable:
    """Manually re-enqueue verification for a Deliverable.

    Used to retry after a ``verification_failed`` terminal, to revalidate
    after dependency changes, or to kick off the first verification when
    the orchestrator's auto-enqueue was unavailable.

    422 if the Deliverable has no ``verifier_type`` configured (we have
    nothing to dispatch). 503 if no Redis stream manager is attached
    (verifier subsystem disabled).
    """
    stmt = select(Deliverable).where(
        Deliverable.id == deliverable_id,
        Deliverable.tenant_id == tenant_id,
    )
    deliverable = (await db.execute(stmt)).scalar_one_or_none()
    if deliverable is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Deliverable not found")

    if deliverable.verifier_type is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Deliverable has no verifier_type configured",
        )

    try:
        verifier_type = VerifierType(deliverable.verifier_type)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Unsupported verifier_type {deliverable.verifier_type!r}",
        ) from exc

    stream_manager = getattr(request.app.state, "stream_manager", None)
    if stream_manager is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Verifier subsystem not available",
        )

    envelope = VerificationEnvelope(
        deliverable_id=deliverable.id,
        tenant_id=deliverable.tenant_id,
        project_id=deliverable.project_id,
        verifier_type=verifier_type,
        inputs=dict(deliverable.verifier_inputs or {}),
    )
    await enqueue_verification(stream_manager, envelope)

    return deliverable
