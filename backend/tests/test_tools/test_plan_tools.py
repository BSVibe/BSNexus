"""Tests for CreateTaskTool — duplicate prevention and self-assign guard."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
import pytest_asyncio

from backend.src.models import Agent, Phase, PhaseStatus, Project, Task, TaskStatus, TaskType
from backend.src.models.tenant import Tenant
from backend.src.tools.base import ToolContext, ToolExecutionError
from backend.src.tools.plan_tools import CreateTaskTool


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def plan_env(test_session_maker):
    """Seed DB with tenant, project, phase, and agents. Return IDs."""
    tenant_id = uuid.uuid4()
    project_id = uuid.uuid4()
    phase_id = uuid.uuid4()
    phase2_id = uuid.uuid4()
    cmo_id = uuid.uuid4()
    designer_id = uuid.uuid4()

    async with test_session_maker() as session:
        session.add(Tenant(id=tenant_id, name="T", slug="t", owner_user_id="u1"))
        session.add(Project(id=project_id, name="P", description="d"))
        await session.flush()
        session.add(Phase(
            id=phase_id, project_id=project_id, name="Phase 1",
            status=PhaseStatus.active, order=1, branch_name="phase/1",
        ))
        session.add(Phase(
            id=phase2_id, project_id=project_id, name="Phase 2",
            status=PhaseStatus.pending, order=2, branch_name="phase/2",
        ))
        session.add(Agent(
            id=cmo_id, tenant_id=tenant_id, name="CMO", role="cmo",
            executor_type="generic_llm", capabilities=["plan"], is_active=True,
        ))
        session.add(Agent(
            id=designer_id, tenant_id=tenant_id, name="Designer", role="designer",
            executor_type="generic_llm", capabilities=["design"], is_active=True,
        ))
        await session.commit()

    return {
        "tenant_id": tenant_id,
        "project_id": project_id,
        "phase_id": phase_id,
        "phase2_id": phase2_id,
        "cmo_id": cmo_id,
        "designer_id": designer_id,
    }


@pytest_asyncio.fixture
async def ctx(plan_env, test_session_maker, tmp_path: Path) -> ToolContext:
    """Build a ToolContext wired to the seeded test DB."""
    return ToolContext(
        project_id=plan_env["project_id"],
        workspace_path=tmp_path,
        workspace_type="server_managed",
        agent_id=plan_env["cmo_id"],
        agent_name="CMO",
        tenant_id=plan_env["tenant_id"],
        db_session_factory=test_session_maker,
    )


# ── Issue #5: Duplicate Prevention ────────────────────────────────────


class TestCreateTaskDuplicatePrevention:
    """CreateTaskTool must detect duplicate titles within the same phase."""

    @pytest.mark.asyncio
    async def test_exact_duplicate_returns_existing(self, ctx: ToolContext) -> None:
        tool = CreateTaskTool()
        result1 = await tool.execute({"title": "Build API"}, ctx)
        data1 = json.loads(result1)
        assert data1["status"] == "pending"

        result2 = await tool.execute({"title": "Build API"}, ctx)
        data2 = json.loads(result2)
        assert data2["task_id"] == data1["task_id"]
        assert "already exists" in data2.get("message", "")
        # Counter should only increment once (first creation)
        assert ctx.tasks_created_this_turn == 1

    @pytest.mark.asyncio
    async def test_case_insensitive_duplicate(self, ctx: ToolContext) -> None:
        tool = CreateTaskTool()
        result1 = await tool.execute({"title": "Build API"}, ctx)
        data1 = json.loads(result1)

        result2 = await tool.execute({"title": "build api"}, ctx)
        data2 = json.loads(result2)
        assert data2["task_id"] == data1["task_id"]
        assert "already exists" in data2.get("message", "")

    @pytest.mark.asyncio
    async def test_similar_title_raises_error(self, ctx: ToolContext) -> None:
        tool = CreateTaskTool()
        await tool.execute({"title": "Build REST API"}, ctx)

        with pytest.raises(ToolExecutionError, match="similar task"):
            await tool.execute({"title": "Build the REST API"}, ctx)

    @pytest.mark.asyncio
    async def test_different_title_succeeds(self, ctx: ToolContext) -> None:
        tool = CreateTaskTool()
        result1 = await tool.execute({"title": "Build API"}, ctx)
        result2 = await tool.execute({"title": "Design Homepage"}, ctx)
        data1 = json.loads(result1)
        data2 = json.loads(result2)
        assert data1["task_id"] != data2["task_id"]
        assert ctx.tasks_created_this_turn == 2

    @pytest.mark.asyncio
    async def test_same_title_different_phase(self, ctx: ToolContext, plan_env: dict) -> None:
        tool = CreateTaskTool()
        result1 = await tool.execute({"title": "Setup"}, ctx)
        result2 = await tool.execute({"title": "Setup", "phase_name": "Phase 2"}, ctx)
        data1 = json.loads(result1)
        data2 = json.loads(result2)
        assert data1["task_id"] != data2["task_id"]

    @pytest.mark.asyncio
    async def test_done_task_allows_recreation(self, ctx: ToolContext) -> None:
        tool = CreateTaskTool()
        result1 = await tool.execute({"title": "Build API"}, ctx)
        task_id = json.loads(result1)["task_id"]

        # Transition task to done
        async with ctx.db_session_factory() as db:
            task = await db.get(Task, uuid.UUID(task_id))
            task.status = TaskStatus.done
            await db.commit()

        # Should allow re-creation
        result2 = await tool.execute({"title": "Build API"}, ctx)
        data2 = json.loads(result2)
        assert data2["task_id"] != task_id
        assert "already exists" not in data2.get("message", "")


# ── Issue #6: Self-Assign Prevention ──────────────────────────────────


class TestCreateTaskSelfAssignPrevention:
    """Active mode agents must not assign tasks to themselves."""

    @pytest.mark.asyncio
    async def test_self_assign_resets_assignee(self, ctx: ToolContext) -> None:
        tool = CreateTaskTool()
        result = await tool.execute({"title": "Plan launch", "assignee": "CMO"}, ctx)
        assert "self-assignment blocked" in result
        data = json.loads(result.split(" (warning")[0])
        assert data["assigned_to"] != "CMO"

    @pytest.mark.asyncio
    async def test_self_assign_case_insensitive(self, ctx: ToolContext) -> None:
        tool = CreateTaskTool()
        result = await tool.execute({"title": "Plan strategy", "assignee": "cmo"}, ctx)
        assert "self-assignment blocked" in result

    @pytest.mark.asyncio
    async def test_assign_other_succeeds(self, ctx: ToolContext) -> None:
        tool = CreateTaskTool()
        result = await tool.execute({"title": "Design mockups", "assignee": "Designer"}, ctx)
        data = json.loads(result)
        assert data["assigned_to"] == "Designer"
        assert "self-assignment" not in result
