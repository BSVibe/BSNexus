"""Tests for GoalAlignmentService and BudgetService."""

from __future__ import annotations

import uuid

import pytest

from backend.src.core.budget import BudgetService
from backend.src.core.goal_alignment import GoalAlignmentService
from backend.src.models import Agent, Goal, Tenant


_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


@pytest.fixture
async def tenant(db_session):
    t = Tenant(id=_TENANT_ID, name="Test", slug="test", owner_user_id="user-1")
    db_session.add(t)
    await db_session.flush()
    return t


@pytest.fixture
async def agent(db_session, tenant):
    a = Agent(
        tenant_id=_TENANT_ID,
        name="Test Bot",
        role="engineer",
        executor_type="generic_llm",
        capabilities=["coding"],
        monthly_budget_cents=10000,  # $100
        current_month_spent_cents=0,
    )
    db_session.add(a)
    await db_session.flush()
    return a


class TestGoalAlignmentService:
    @pytest.mark.asyncio
    async def test_get_ancestry_single(self, db_session, tenant) -> None:
        goal = Goal(tenant_id=_TENANT_ID, level="mission", title="Build great products")
        db_session.add(goal)
        await db_session.flush()

        service = GoalAlignmentService(db_session)
        chain = await service.get_goal_ancestry(goal.id)
        assert len(chain) == 1
        assert chain[0].title == "Build great products"

    @pytest.mark.asyncio
    async def test_get_ancestry_chain(self, db_session, tenant) -> None:
        mission = Goal(tenant_id=_TENANT_ID, level="mission", title="Mission")
        db_session.add(mission)
        await db_session.flush()

        dept = Goal(tenant_id=_TENANT_ID, level="department", title="Engineering", parent_goal_id=mission.id)
        db_session.add(dept)
        await db_session.flush()

        task_goal = Goal(tenant_id=_TENANT_ID, level="task", title="Fix bug #123", parent_goal_id=dept.id)
        db_session.add(task_goal)
        await db_session.flush()

        service = GoalAlignmentService(db_session)
        chain = await service.get_goal_ancestry(task_goal.id)
        assert len(chain) == 3
        assert chain[0].level == "mission"
        assert chain[1].level == "department"
        assert chain[2].level == "task"

    @pytest.mark.asyncio
    async def test_build_goal_context_formats_chain(self, db_session, tenant) -> None:
        mission = Goal(tenant_id=_TENANT_ID, level="mission", title="Ship v2")
        db_session.add(mission)
        await db_session.flush()

        task_goal = Goal(tenant_id=_TENANT_ID, level="task", title="Auth module", parent_goal_id=mission.id)
        db_session.add(task_goal)
        await db_session.flush()

        service = GoalAlignmentService(db_session)
        context = await service.build_goal_context(task_goal.id)
        assert "Goal Alignment" in context
        assert "Mission: Ship v2" in context
        assert "Task: Auth module" in context

    @pytest.mark.asyncio
    async def test_build_goal_context_none(self, db_session) -> None:
        service = GoalAlignmentService(db_session)
        context = await service.build_goal_context(None)
        assert context == ""

    @pytest.mark.asyncio
    async def test_inject_goal_context(self, db_session, tenant) -> None:
        goal = Goal(tenant_id=_TENANT_ID, level="mission", title="Launch product")
        db_session.add(goal)
        await db_session.flush()

        service = GoalAlignmentService(db_session)
        prompt = await service.inject_goal_context("Write code for auth", goal.id)
        assert "Goal Alignment" in prompt
        assert "Write code for auth" in prompt


class TestBudgetService:
    @pytest.mark.asyncio
    async def test_check_budget_within_limit(self, db_session, agent) -> None:
        service = BudgetService(db_session)
        assert await service.check_budget(agent.id) is True

    @pytest.mark.asyncio
    async def test_check_budget_exceeded(self, db_session, agent) -> None:
        agent.current_month_spent_cents = 10000  # at limit
        await db_session.flush()

        service = BudgetService(db_session)
        assert await service.check_budget(agent.id) is False

    @pytest.mark.asyncio
    async def test_check_budget_no_limit(self, db_session, tenant) -> None:
        a = Agent(
            tenant_id=_TENANT_ID, name="Unlimited", role="eng",
            executor_type="generic_llm", capabilities=["coding"],
            monthly_budget_cents=None,
        )
        db_session.add(a)
        await db_session.flush()

        service = BudgetService(db_session)
        assert await service.check_budget(a.id) is True

    @pytest.mark.asyncio
    async def test_record_cost(self, db_session, agent) -> None:
        service = BudgetService(db_session)
        record = await service.record_cost(
            tenant_id=_TENANT_ID,
            agent_id=agent.id,
            amount_cents=500,
            token_count=1000,
            model_name="claude-sonnet-4-6",
        )
        assert record.amount_cents == 500
        await db_session.refresh(agent)
        assert agent.current_month_spent_cents == 500

    @pytest.mark.asyncio
    async def test_record_cost_triggers_budget_exceeded(self, db_session, agent) -> None:
        agent.current_month_spent_cents = 9800
        await db_session.flush()

        service = BudgetService(db_session)
        await service.record_cost(tenant_id=_TENANT_ID, agent_id=agent.id, amount_cents=300)

        await db_session.refresh(agent)
        assert agent.current_month_spent_cents == 10100
        assert agent.status == "budget_exceeded"

    @pytest.mark.asyncio
    async def test_check_budget_nonexistent_agent(self, db_session) -> None:
        service = BudgetService(db_session)
        assert await service.check_budget(uuid.uuid4()) is False
