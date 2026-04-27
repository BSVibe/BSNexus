"""Decisions API — approval inbox per project + resolve single."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from bsvibe_audit.events.nexus import DecisionResolved
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.audit import actor_from_user, resource_decision, safe_emit
from backend.src.core.auth import get_current_user
from backend.src.core.composer import resolve_knowledge_client
from backend.src.core.integrations import get_tenant_integration_snapshot
from backend.src.core.tenant_context import get_tenant_id
from backend.src.models import Decision, Project
from backend.src.schemas import DecisionResolve, DecisionResponse
from backend.src.storage.database import get_db

project_router = APIRouter(prefix="/api/v1/projects", tags=["decisions"])
decision_router = APIRouter(prefix="/api/v1/decisions", tags=["decisions"])


async def _assert_project_belongs(db: AsyncSession, project_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
    stmt = select(Project.id).where(Project.id == project_id, Project.tenant_id == tenant_id)
    if (await db.execute(stmt)).scalar_one_or_none() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")


@project_router.get(
    "/{project_id}/decisions",
    response_model=list[DecisionResponse],
)
async def list_decisions(
    project_id: uuid.UUID,
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> list[Decision]:
    """Open decisions first (blocking → non-blocking), then resolved."""
    await _assert_project_belongs(db, project_id, tenant_id)
    stmt = (
        select(Decision)
        .where(
            Decision.project_id == project_id,
            Decision.tenant_id == tenant_id,
        )
        .order_by(
            Decision.resolved_at.is_(None).desc(),
            Decision.blocking.desc(),
            Decision.created_at.desc(),
        )
    )
    return list((await db.execute(stmt)).scalars())


@decision_router.post(
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

    decision.resolution = payload.resolution
    decision.resolved_by = payload.resolved_by
    decision.resolved_at = datetime.now(timezone.utc)

    # Phase Audit Batch 2 — emit ``nexus.decision.resolved`` in the same
    # transaction as the resolution UPDATE. Actor is the founder (the
    # authenticated user) — Decision resolution is always a direct user
    # action through this endpoint.
    await safe_emit(
        DecisionResolved(
            actor=actor_from_user(user),
            tenant_id=str(tenant_id),
            resource=resource_decision(decision.id),
            data={
                "project_id": str(decision.project_id),
                "request_id": str(decision.request_id) if decision.request_id is not None else None,
                "resolution": payload.resolution,
                "resolved_by": payload.resolved_by,
                "blocking": decision.blocking,
            },
        ),
        session=db,
    )

    await db.commit()
    await db.refresh(decision)

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
