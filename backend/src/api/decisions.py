from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.auth import get_current_user, require_permission
from backend.src.core.tenant_context import get_tenant_id
from backend.src.models import Decision, Project
from backend.src.queue.streams import RedisStreamManager
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
    dependencies=[Depends(require_permission("bsnexus.decisions.read"))],
)
async def list_decisions(
    project_id: uuid.UUID | None = Query(None),
    blocking_only: bool = Query(False),
    resolved: bool | None = Query(None),
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> list[Decision]:
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


@router.post(
    "/{decision_id}/resolve",
    response_model=DecisionResponse,
    dependencies=[Depends(require_permission("bsnexus.decisions.write"))],
)
async def resolve_decision(
    decision_id: uuid.UUID,
    payload: DecisionResolve,
    request: Request,
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> Decision:
    stmt = select(Decision).where(Decision.id == decision_id, Decision.tenant_id == tenant_id)
    decision = (await db.execute(stmt)).scalar_one_or_none()
    if decision is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Decision not found")

    decision.resolution = payload.resolution
    decision.resolved_by = payload.resolved_by
    decision.resolved_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(decision)

    # G7.2 — fan the resolution onto the project's SSE stream so any
    # other open BSNexus tab dismisses the row without a manual click.
    # Frontend ``useProjectEvents`` handler invalidates the
    # ``['decisions', projectId]`` and ``['runs', projectId]`` queries
    # on this event.
    stream_manager: RedisStreamManager = request.app.state.stream_manager
    await stream_manager.publish_project_event(
        str(decision.project_id),
        "decision_resolved",
        {
            "id": str(decision.id),
            "project_id": str(decision.project_id),
            "resolution": decision.resolution,
            "resolved_by": decision.resolved_by,
            "resolved_at": decision.resolved_at.isoformat() if decision.resolved_at else None,
        },
    )
    return decision
