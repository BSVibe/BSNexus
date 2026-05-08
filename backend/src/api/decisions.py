"""Decisions API — flat resource shape (decision-locks A3, 2026-05-08).

- ``GET /api/v1/decisions?project_id={id}&blocking_only=&resolved=&limit=``
  - ``project_id`` omitted → tenant-wide cross-project (Home Decision Inbox).
  - ``blocking_only=true`` filters to ``blocking AND resolved_at IS NULL``.
  - ``resolved=false`` (default) hides resolved rows; ``resolved=true`` shows them.
- ``POST /api/v1/decisions/{decision_id}/resolve`` — unchanged.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from bsvibe_audit import audit_emit
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.audit import actor_from_user, get_emitter
from backend.src.core.auth import get_current_user
from backend.src.core.composer import resolve_knowledge_client
from backend.src.core.integrations import get_tenant_integration_snapshot
from backend.src.core.tenant_context import get_tenant_id
from backend.src.models import Decision, Project
from backend.src.schemas import DecisionResolve, DecisionResponse
from backend.src.storage.database import get_db

router = APIRouter(prefix="/api/v1/decisions", tags=["decisions"])

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


async def _assert_project_belongs(db: AsyncSession, project_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
    stmt = select(Project.id).where(Project.id == project_id, Project.tenant_id == tenant_id)
    if (await db.execute(stmt)).scalar_one_or_none() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")


@router.get(
    "",
    response_model=list[DecisionResponse],
)
async def list_decisions(
    project_id: uuid.UUID | None = Query(
        None, description="Filter to a single project. Omit for the tenant-wide inbox."
    ),
    blocking_only: bool = Query(False, description="When true, return only unresolved blocking decisions."),
    resolved: bool | None = Query(
        None,
        description=(
            "Tri-state filter for resolved rows. None (default) returns open + resolved sorted "
            "open-first. true returns only resolved. false returns only unresolved."
        ),
    ),
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> list[Decision]:
    """Decision inbox.

    Default ordering is open-first → blocking-first → newest-first, matching the
    behaviour the per-project inbox relied on. Cross-project callers (Home strip,
    Slack digest, voice) get the same ordering.
    """
    if project_id is not None:
        await _assert_project_belongs(db, project_id, tenant_id)

    stmt = select(Decision).where(Decision.tenant_id == tenant_id)
    if project_id is not None:
        stmt = stmt.where(Decision.project_id == project_id)

    if blocking_only:
        stmt = stmt.where(Decision.blocking.is_(True), Decision.resolved_at.is_(None))
    elif resolved is True:
        stmt = stmt.where(Decision.resolved_at.is_not(None))
    elif resolved is False:
        stmt = stmt.where(Decision.resolved_at.is_(None))

    stmt = stmt.order_by(
        Decision.resolved_at.is_(None).desc(),
        Decision.blocking.desc(),
        Decision.created_at.desc(),
    ).limit(limit)

    return list((await db.execute(stmt)).scalars())


# Phase Audit Batch 3 — Audit-3 expansion site #2. The decorator emits
# ``nexus.decision.resolved`` against ``db`` *before* the outer handler
# commits, keeping the resolution UPDATE + the outbox INSERT in the
# same transaction. ``safe=True`` preserves the previous ``safe_emit``
# failure semantics; ``actor_factory=actor_from_user`` keeps the
# native ``user`` kwarg.
@audit_emit(
    "nexus.decision.resolved",
    emitter=get_emitter(),
    resource_type="decision",
    resource_id_attr="id",
    actor_factory=actor_from_user,
    actor_kwarg="user",
    data_extractor=lambda _args, kwargs, decision: {
        "project_id": str(decision.project_id),
        "request_id": str(decision.request_id) if decision.request_id is not None else None,
        "resolution": kwargs["payload"].resolution,
        "resolved_by": kwargs["payload"].resolved_by,
        "blocking": decision.blocking,
    },
    safe=True,
)
async def _apply_decision_resolution_with_audit(
    *,
    decision: Decision,
    payload: DecisionResolve,
    user,  # type: ignore[no-untyped-def]
    tenant_id: uuid.UUID,
    session: AsyncSession,
) -> Decision:
    decision.resolution = payload.resolution
    decision.resolved_by = payload.resolved_by
    decision.resolved_at = datetime.now(timezone.utc)
    # No commit here — the outer handler commits after the decorator
    # has emitted the outbox row, keeping the UPDATE + the audit row
    # atomic (BSVibe_Audit_Design.md §3.1).
    await session.flush()
    return decision


@router.post(
    "/{decision_id}/resolve",
    response_model=DecisionResponse,
)
async def resolve_decision(
    decision_id: uuid.UUID,
    payload: DecisionResolve,
    request: Request,
    user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> Decision:
    stmt = select(Decision).where(Decision.id == decision_id, Decision.tenant_id == tenant_id)
    decision = (await db.execute(stmt)).scalar_one_or_none()
    if decision is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Decision not found")

    decision = await _apply_decision_resolution_with_audit(
        decision=decision,
        payload=payload,
        user=user,
        tenant_id=tenant_id,
        session=db,
    )

    await db.commit()
    await db.refresh(decision)

    # Direction reset 2026-05-03 — unblock any MCP ``decision.wait``
    # callers parked on this decision_id. The BSGateway worker's claude
    # CLI resumes its run with the founder's choice as the tool result.
    from backend.src.core.project_events import publish_decision_resolved  # noqa: PLC0415
    from backend.src.mcp import get_decision_queue  # noqa: PLC0415

    get_decision_queue().notify(
        decision.id,
        result={
            "choice": decision.resolution or "",
            "notes": "",
        },
    )

    # SSE fan-out so the Decisions tab can dismiss the row immediately
    # and the Inside panel can flip its blocked banner.
    await publish_decision_resolved(
        decision.project_id,
        decision_id=decision.id,
        resolution=decision.resolution or "",
        resolved_by=decision.resolved_by,
    )

    integrations = await get_tenant_integration_snapshot(db, tenant_id)
    knowledge = resolve_knowledge_client(integrations.bsage)
    project = (await db.execute(select(Project).where(Project.id == decision.project_id))).scalar_one_or_none()
    project_name = project.name if project is not None else "project"

    auth_header = request.headers.get("authorization") or request.headers.get("Authorization", "")
    auth_token: str | None = None
    if auth_header.lower().startswith("bearer "):
        auth_token = auth_header.split(" ", 1)[1].strip() or None

    await knowledge.record_decision(
        title=decision.question[:200] or f"Decision {decision.id}",
        decision=payload.resolution,
        reasoning=f"Resolved by {payload.resolved_by or 'founder'}",
        alternatives=list(decision.options or []),
        context=f"Project: {project_name}",
        tags=[
            f"project:{project_name.lower().replace(' ', '-')}",
            "bsnexus-decision",
            "blocking" if decision.blocking else "non-blocking",
        ],
        source=f"bsnexus:{project_name}",
        auth_token=auth_token,
    )

    return decision
