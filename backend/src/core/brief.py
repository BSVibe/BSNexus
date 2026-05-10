from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.domain import BriefScope, DeliverableStatus, ProofState, RequestStatus
from backend.src.models import Decision, Deliverable, Request


def empty_brief_snapshot(project_id: uuid.UUID | None = None) -> dict:
    return {
        "scope": BriefScope.project.value if project_id is not None else BriefScope.company.value,
        "project_id": project_id,
        "sections": {
            "shipped": [],
            "needs_decision": [],
            "blocked": [],
            "running": [],
            "next": [],
        },
        "generated_at": datetime.now(timezone.utc),
    }


async def build_brief_snapshot(
    *,
    session: AsyncSession,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID | None = None,
    limit_per_section: int = 10,
) -> dict:
    return {
        "scope": BriefScope.project.value if project_id is not None else BriefScope.company.value,
        "project_id": project_id,
        "sections": {
            "shipped": await _shipped_cards(session, tenant_id, project_id, limit_per_section),
            "needs_decision": await _decision_cards(session, tenant_id, project_id, limit_per_section),
            "blocked": await _blocked_cards(session, tenant_id, project_id, limit_per_section),
            "running": await _request_cards(session, tenant_id, project_id, RequestStatus.running, limit_per_section),
            "next": await _request_cards(session, tenant_id, project_id, RequestStatus.open, limit_per_section),
        },
        "generated_at": datetime.now(timezone.utc),
    }


async def _shipped_cards(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID | None,
    limit: int,
) -> list[dict]:
    stmt = (
        select(Deliverable)
        .where(
            Deliverable.tenant_id == tenant_id,
            Deliverable.status == DeliverableStatus.shipped,
            Deliverable.proof_state == ProofState.verified,
        )
        .order_by(Deliverable.updated_at.desc())
        .limit(limit)
    )
    if project_id is not None:
        stmt = stmt.where(Deliverable.project_id == project_id)
    deliverables = (await session.execute(stmt)).scalars().all()
    return [_deliverable_card(deliverable, label="shipped") for deliverable in deliverables]


async def _decision_cards(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID | None,
    limit: int,
) -> list[dict]:
    stmt = (
        select(Decision)
        .where(Decision.tenant_id == tenant_id, Decision.resolved_at.is_(None))
        .order_by(Decision.blocking.desc(), Decision.created_at.desc())
        .limit(limit)
    )
    if project_id is not None:
        stmt = stmt.where(Decision.project_id == project_id)
    decisions = (await session.execute(stmt)).scalars().all()
    return [
        {
            "kind": "decision",
            "id": str(decision.id),
            "project_id": str(decision.project_id),
            "request_id": str(decision.request_id) if decision.request_id else None,
            "work_step_id": str(decision.work_step_id) if decision.work_step_id else None,
            "title": decision.question,
            "blocking": decision.blocking,
            "created_at": decision.created_at,
        }
        for decision in decisions
    ]


async def _blocked_cards(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID | None,
    limit: int,
) -> list[dict]:
    cards = await _request_cards(session, tenant_id, project_id, RequestStatus.blocked, limit)
    remaining = max(limit - len(cards), 0)
    if remaining == 0:
        return cards

    stmt = (
        select(Deliverable)
        .where(
            Deliverable.tenant_id == tenant_id,
            Deliverable.proof_state.in_(
                [
                    ProofState.verification_failed,
                    ProofState.human_review_required,
                    ProofState.verification_missing,
                ]
            ),
            Deliverable.status == DeliverableStatus.shipped,
        )
        .order_by(Deliverable.updated_at.desc())
        .limit(remaining)
    )
    if project_id is not None:
        stmt = stmt.where(Deliverable.project_id == project_id)
    deliverables = (await session.execute(stmt)).scalars().all()
    cards.extend(_deliverable_card(deliverable, label="blocked") for deliverable in deliverables)
    return cards


async def _request_cards(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID | None,
    status: RequestStatus,
    limit: int,
) -> list[dict]:
    stmt = (
        select(Request)
        .where(Request.tenant_id == tenant_id, Request.status == status)
        .order_by(Request.updated_at.desc())
        .limit(limit)
    )
    if project_id is not None:
        stmt = stmt.where(Request.project_id == project_id)
    requests = (await session.execute(stmt)).scalars().all()
    return [
        {
            "kind": "request",
            "id": str(request.id),
            "project_id": str(request.project_id),
            "request_id": str(request.id),
            "title": request.intent,
            "status": request.status.value,
            "current_step_id": str(request.current_step_id) if request.current_step_id else None,
            "updated_at": request.updated_at,
        }
        for request in requests
    ]


def _deliverable_card(deliverable: Deliverable, *, label: str) -> dict:
    return {
        "kind": "deliverable",
        "id": str(deliverable.id),
        "project_id": str(deliverable.project_id),
        "request_id": str(deliverable.request_id) if deliverable.request_id else None,
        "work_step_id": str(deliverable.work_step_id) if deliverable.work_step_id else None,
        "title": deliverable.title,
        "type": deliverable.type.value,
        "status": deliverable.status.value,
        "proof_state": deliverable.proof_state.value,
        "label": label,
        "updated_at": deliverable.updated_at,
    }
