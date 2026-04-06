"""Budget and cost tracking schemas."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class CostRecordCreate(BaseModel):
    agent_id: uuid.UUID
    task_id: Optional[uuid.UUID] = None
    amount_cents: int
    token_count: Optional[int] = None
    model_name: Optional[str] = None


class CostRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    agent_id: uuid.UUID
    task_id: Optional[uuid.UUID] = None
    amount_cents: int
    token_count: Optional[int] = None
    model_name: Optional[str] = None
    recorded_at: datetime


class AgentBudgetSummary(BaseModel):
    agent_id: uuid.UUID
    agent_name: str
    monthly_budget_cents: Optional[int] = None
    current_month_spent_cents: int = 0
    budget_remaining_cents: Optional[int] = None
    utilization_pct: Optional[float] = None


class BudgetOverviewResponse(BaseModel):
    total_budget_cents: int
    total_spent_cents: int
    total_remaining_cents: int
    agent_summaries: list[AgentBudgetSummary]
