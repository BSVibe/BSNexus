"""Brief endpoint — server-side aggregation of the 5 founder sections.

Decision-locks **A2** (locked 2026-05-08): one round-trip, snapshot
consistency, interface-agnostic payload. Reused by web, mobile/PWA,
Slack ``/bsnexus brief``, email digests, and voice "give me the brief"
once those clients land.

- ``GET /api/v1/brief``                 → company Brief (tenant-scoped)
- ``GET /api/v1/brief?project_id={id}`` → project Brief

Sections (locked in core-ux-spec.md §Brief UX):

- **shipped** — Deliverables with ``proof_state = verified``, newest
  first.
- **needs_decision** — open Decisions, blocking first.
- **blocked** — ExecutionRuns currently in the ``blocked`` state.
- **running** — ExecutionRuns in ``pending`` or ``running``.
- **next** — recommended next direction (LLM-derived; empty in PR4).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.auth import get_current_user
from backend.src.core.tenant_context import get_tenant_id
from backend.src.models import (
    Decision,
    Deliverable,
    ExecutionRun,
    ProofState,
    Project,
    Request,
    RunStatus,
)
from backend.src.schemas.brief import (
    BriefDecision,
    BriefDeliverable,
    BriefNextHint,
    BriefResponse,
    BriefRun,
)
from backend.src.storage.database import get_db

router = APIRouter(prefix="/api/v1/brief", tags=["brief"])

_DEFAULT_LIMIT = 10
_MAX_LIMIT = 50


async def _assert_project_belongs(db: AsyncSession, project_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
    stmt = select(Project.id).where(Project.id == project_id, Project.tenant_id == tenant_id)
    if (await db.execute(stmt)).scalar_one_or_none() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")


@router.get(
    "",
    response_model=BriefResponse,
)
async def get_brief(
    project_id: uuid.UUID | None = Query(
        None,
        description="Filter to a single project. Omit for the tenant-wide company Brief.",
    ),
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> BriefResponse:
    if project_id is not None:
        await _assert_project_belongs(db, project_id, tenant_id)

    shipped = await _shipped(db, tenant_id, project_id, limit)
    needs_decision = await _needs_decision(db, tenant_id, project_id, limit)
    blocked = await _runs_in_status(db, tenant_id, project_id, [RunStatus.blocked], limit)
    running = await _runs_in_status(db, tenant_id, project_id, [RunStatus.running, RunStatus.pending], limit)

    return BriefResponse(
        project_id=project_id,
        generated_at=datetime.now(timezone.utc),
        shipped=shipped,
        needs_decision=needs_decision,
        blocked=blocked,
        running=running,
        next=[],  # populated once the LLM-derived hint pipeline lands
    )


async def _shipped(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID | None,
    limit: int,
) -> list[BriefDeliverable]:
    stmt = (
        select(Deliverable)
        .where(
            Deliverable.tenant_id == tenant_id,
            Deliverable.proof_state == ProofState.verified,
        )
        .order_by(Deliverable.verified_at.desc().nulls_last(), Deliverable.created_at.desc())
        .limit(limit)
    )
    if project_id is not None:
        stmt = stmt.where(Deliverable.project_id == project_id)
    rows = list((await db.execute(stmt)).scalars())
    return [BriefDeliverable.model_validate(r, from_attributes=True) for r in rows]


async def _needs_decision(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID | None,
    limit: int,
) -> list[BriefDecision]:
    stmt = (
        select(Decision)
        .where(
            Decision.tenant_id == tenant_id,
            Decision.resolved_at.is_(None),
        )
        .order_by(Decision.blocking.desc(), Decision.created_at.desc())
        .limit(limit)
    )
    if project_id is not None:
        stmt = stmt.where(Decision.project_id == project_id)
    rows = list((await db.execute(stmt)).scalars())
    return [BriefDecision.model_validate(r, from_attributes=True) for r in rows]


async def _runs_in_status(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID | None,
    statuses: list[RunStatus],
    limit: int,
) -> list[BriefRun]:
    stmt = (
        select(ExecutionRun, Request.intent_summary)
        .join(Request, Request.id == ExecutionRun.request_id, isouter=True)
        .where(
            ExecutionRun.tenant_id == tenant_id,
            ExecutionRun.status.in_(statuses),
        )
        .order_by(ExecutionRun.created_at.desc())
        .limit(limit)
    )
    if project_id is not None:
        stmt = stmt.where(ExecutionRun.project_id == project_id)

    rows = list((await db.execute(stmt)).all())
    out: list[BriefRun] = []
    for run, intent in rows:
        out.append(
            BriefRun(
                id=run.id,
                project_id=run.project_id,
                request_id=run.request_id,
                request_intent=intent,
                status=run.status,
                started_at=run.started_at,
                created_at=run.created_at,
                error_message=run.error_message,
            )
        )
    return out


# `BriefNextHint` is exported for forward-compatible serialization tests.
__all__ = ["router", "BriefNextHint"]
