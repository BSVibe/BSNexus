"""Budget API — agent budget summaries and cost records."""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.budget import BudgetService
from backend.src.core.tenant_context import get_tenant_id
from backend.src.models import Agent, CostRecord
from backend.src.schemas.budget import AgentBudgetSummary, BudgetOverviewResponse, CostRecordResponse
from backend.src.storage.database import get_db

router = APIRouter(prefix="/api/v1/budget", tags=["budget"])


@router.get("/summary", response_model=BudgetOverviewResponse)
async def get_budget_summary(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> BudgetOverviewResponse:
    """Return budget overview with per-agent summaries."""
    result = await db.execute(
        select(Agent).where(Agent.tenant_id == tenant_id, Agent.is_active.is_(True))
    )
    agents = result.scalars().all()

    total_budget = 0
    total_spent = 0
    summaries: list[AgentBudgetSummary] = []

    for agent in agents:
        spent = agent.current_month_spent_cents
        budget = agent.monthly_budget_cents
        total_spent += spent
        if budget is not None:
            total_budget += budget
            remaining = budget - spent
            utilization = (spent / budget * 100) if budget > 0 else 0.0
        else:
            remaining = None
            utilization = None

        summaries.append(
            AgentBudgetSummary(
                agent_id=agent.id,
                agent_name=agent.name,
                monthly_budget_cents=budget,
                current_month_spent_cents=spent,
                budget_remaining_cents=remaining,
                utilization_pct=utilization,
            )
        )

    return BudgetOverviewResponse(
        total_budget_cents=total_budget,
        total_spent_cents=total_spent,
        total_remaining_cents=total_budget - total_spent,
        agent_summaries=summaries,
    )


@router.get("/records", response_model=list[CostRecordResponse])
async def get_cost_records(
    agent_id: Optional[uuid.UUID] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[CostRecordResponse]:
    """Return cost records, optionally filtered by agent_id."""
    query = select(CostRecord).order_by(CostRecord.recorded_at.desc())
    if agent_id is not None:
        query = query.where(CostRecord.agent_id == agent_id)
    query = query.limit(limit).offset(offset)

    result = await db.execute(query)
    return [CostRecordResponse.model_validate(r) for r in result.scalars().all()]


@router.post("/reset")
async def reset_monthly_budgets(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> dict:
    """Reset all agents' monthly spend counters."""
    service = BudgetService(db)
    count = await service.reset_monthly_budgets(tenant_id)
    await db.commit()
    return {"reset_count": count}
