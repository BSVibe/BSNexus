"""Budget enforcement service — tracks costs and enforces per-agent limits."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.models import Agent, CostRecord

logger = structlog.get_logger(__name__)


class BudgetService:
    """Tracks agent costs and enforces monthly budget caps."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def check_budget(self, agent_id: uuid.UUID) -> bool:
        """Return True if agent is within budget (or has no budget set)."""
        result = await self.db.execute(select(Agent).where(Agent.id == agent_id))
        agent = result.scalar_one_or_none()
        if agent is None:
            return False
        if agent.monthly_budget_cents is None:
            return True  # No limit set
        return agent.current_month_spent_cents < agent.monthly_budget_cents

    async def record_cost(
        self,
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID,
        amount_cents: int,
        *,
        task_id: uuid.UUID | None = None,
        token_count: int | None = None,
        model_name: str | None = None,
    ) -> CostRecord:
        """Record a cost and update agent's running total."""
        record = CostRecord(
            tenant_id=tenant_id,
            agent_id=agent_id,
            task_id=task_id,
            amount_cents=amount_cents,
            token_count=token_count,
            model_name=model_name,
        )
        self.db.add(record)

        # Increment agent's current_month_spent_cents
        await self.db.execute(
            update(Agent)
            .where(Agent.id == agent_id)
            .values(current_month_spent_cents=Agent.current_month_spent_cents + amount_cents)
        )
        await self.db.flush()

        # Check if budget exceeded after recording
        agent_result = await self.db.execute(select(Agent).where(Agent.id == agent_id))
        agent = agent_result.scalar_one_or_none()
        if agent and agent.monthly_budget_cents is not None:
            if agent.current_month_spent_cents >= agent.monthly_budget_cents:
                await self.db.execute(
                    update(Agent).where(Agent.id == agent_id).values(status="budget_exceeded")
                )
                logger.warning(
                    "agent_budget_exceeded",
                    agent_id=str(agent_id),
                    spent=agent.current_month_spent_cents,
                    limit=agent.monthly_budget_cents,
                )

        return record

    async def get_agent_monthly_total(self, agent_id: uuid.UUID) -> int:
        """Get total spend for an agent this month."""
        now = datetime.now(timezone.utc)
        first_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        result = await self.db.execute(
            select(func.coalesce(func.sum(CostRecord.amount_cents), 0)).where(
                CostRecord.agent_id == agent_id,
                CostRecord.recorded_at >= first_of_month,
            )
        )
        return result.scalar_one()

    async def reset_monthly_budgets(self, tenant_id: uuid.UUID) -> int:
        """Reset all agents' monthly spend counters. Returns number of agents reset."""
        result = await self.db.execute(
            update(Agent)
            .where(Agent.tenant_id == tenant_id)
            .values(current_month_spent_cents=0, status="offline")
        )
        await self.db.flush()
        return result.rowcount  # type: ignore[return-value]
