"""Tests for Budget API — agent budget summaries and cost records."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.models import Agent, CostRecord, Tenant

_DEFAULT_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


@pytest_asyncio.fixture(autouse=True)
async def _seed_tenant(db_session):
    """Ensure default tenant exists for all budget tests."""
    t = Tenant(id=_DEFAULT_TENANT_ID, name="Test", slug="test", owner_user_id="user-1")
    db_session.add(t)
    await db_session.flush()
    await db_session.commit()


async def _create_agent(
    db: AsyncSession,
    name: str = "Test Agent",
    role: str = "developer",
    monthly_budget_cents: int | None = 10000,
    current_month_spent_cents: int = 0,
) -> Agent:
    agent = Agent(
        tenant_id=_DEFAULT_TENANT_ID,
        name=name,
        role=role,
        monthly_budget_cents=monthly_budget_cents,
        current_month_spent_cents=current_month_spent_cents,
    )
    db.add(agent)
    await db.flush()
    await db.refresh(agent)
    return agent


async def _create_cost_record(
    db: AsyncSession,
    agent_id: uuid.UUID,
    amount_cents: int = 500,
    model_name: str | None = "claude-3",
    token_count: int | None = 1000,
) -> CostRecord:
    record = CostRecord(
        tenant_id=_DEFAULT_TENANT_ID,
        agent_id=agent_id,
        amount_cents=amount_cents,
        model_name=model_name,
        token_count=token_count,
    )
    db.add(record)
    await db.flush()
    return record


@pytest.mark.asyncio
async def test_get_budget_summary_empty(client):
    """Summary with no agents returns empty agent_summaries."""
    resp = await client.get("/api/v1/budget/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_budget_cents"] == 0
    assert data["total_spent_cents"] == 0
    assert data["total_remaining_cents"] == 0
    assert data["agent_summaries"] == []


@pytest.mark.asyncio
async def test_get_budget_summary_with_agents(client, db_session):
    """Summary returns correct totals and per-agent data."""
    a1 = await _create_agent(db_session, "Agent A", "cto", 10000, 3000)
    a2 = await _create_agent(db_session, "Agent B", "dev", 5000, 1000)
    await db_session.commit()

    resp = await client.get("/api/v1/budget/summary")
    assert resp.status_code == 200
    data = resp.json()

    assert data["total_budget_cents"] == 15000
    assert data["total_spent_cents"] == 4000
    assert data["total_remaining_cents"] == 11000
    assert len(data["agent_summaries"]) == 2

    summaries = {s["agent_name"]: s for s in data["agent_summaries"]}
    assert summaries["Agent A"]["monthly_budget_cents"] == 10000
    assert summaries["Agent A"]["current_month_spent_cents"] == 3000
    assert summaries["Agent A"]["budget_remaining_cents"] == 7000
    assert summaries["Agent A"]["utilization_pct"] == pytest.approx(30.0)

    assert summaries["Agent B"]["utilization_pct"] == pytest.approx(20.0)


@pytest.mark.asyncio
async def test_get_budget_summary_no_budget_agent(client, db_session):
    """Agent without monthly_budget_cents has null remaining and utilization."""
    await _create_agent(db_session, "Unbounded", "researcher", None, 2000)
    await db_session.commit()

    resp = await client.get("/api/v1/budget/summary")
    assert resp.status_code == 200
    data = resp.json()

    assert data["total_budget_cents"] == 0
    assert data["total_spent_cents"] == 2000
    assert len(data["agent_summaries"]) == 1
    s = data["agent_summaries"][0]
    assert s["monthly_budget_cents"] is None
    assert s["budget_remaining_cents"] is None
    assert s["utilization_pct"] is None


@pytest.mark.asyncio
async def test_get_cost_records_empty(client):
    """No records returns empty list."""
    resp = await client.get("/api/v1/budget/records")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_get_cost_records(client, db_session):
    """Returns cost records."""
    agent = await _create_agent(db_session)
    await _create_cost_record(db_session, agent.id, 100, "gpt-4", 500)
    await _create_cost_record(db_session, agent.id, 200, "claude-3", 1000)
    await db_session.commit()

    resp = await client.get("/api/v1/budget/records")
    assert resp.status_code == 200
    records = resp.json()
    assert len(records) == 2
    amounts = sorted([r["amount_cents"] for r in records])
    assert amounts == [100, 200]


@pytest.mark.asyncio
async def test_get_cost_records_filtered_by_agent(client, db_session):
    """Filter records by agent_id."""
    a1 = await _create_agent(db_session, "A1", "dev")
    a2 = await _create_agent(db_session, "A2", "dev")
    await _create_cost_record(db_session, a1.id, 100)
    await _create_cost_record(db_session, a2.id, 200)
    await db_session.commit()

    resp = await client.get(f"/api/v1/budget/records?agent_id={a1.id}")
    assert resp.status_code == 200
    records = resp.json()
    assert len(records) == 1
    assert records[0]["agent_id"] == str(a1.id)


@pytest.mark.asyncio
async def test_get_cost_records_pagination(client, db_session):
    """Limit and offset work correctly."""
    agent = await _create_agent(db_session)
    for i in range(5):
        await _create_cost_record(db_session, agent.id, (i + 1) * 100)
    await db_session.commit()

    resp = await client.get("/api/v1/budget/records?limit=2&offset=0")
    assert resp.status_code == 200
    assert len(resp.json()) == 2

    resp2 = await client.get("/api/v1/budget/records?limit=2&offset=2")
    assert resp2.status_code == 200
    assert len(resp2.json()) == 2


@pytest.mark.asyncio
async def test_reset_monthly_budgets(client, db_session):
    """Reset sets all agents' spent to 0."""
    await _create_agent(db_session, "A1", "dev", 10000, 5000)
    await _create_agent(db_session, "A2", "dev", 5000, 3000)
    await db_session.commit()

    resp = await client.post("/api/v1/budget/reset")
    assert resp.status_code == 200
    data = resp.json()
    assert data["reset_count"] == 2

    # Verify via summary
    summary = await client.get("/api/v1/budget/summary")
    assert summary.json()["total_spent_cents"] == 0
