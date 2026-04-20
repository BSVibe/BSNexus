"""Tests for CreateTaskTool — duplicate prevention, self-assign guard, and race handling."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError

from backend.src.models import Agent, Phase, PhaseStatus, Project, Task, TaskStatus, TaskType
from backend.src.models.tenant import Tenant
from backend.src.tools.base import ToolContext, ToolExecutionError
from backend.src.tools.plan_tools import CreateTaskTool, create_phase_from_params


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
        pm_id = uuid.uuid4()
        session.add(Agent(
            id=pm_id, tenant_id=tenant_id, name="PM", role="product_manager",
            executor_type="generic_llm", capabilities=["plan"], is_active=True,
        ))
        await session.commit()

    return {
        "tenant_id": tenant_id,
        "project_id": project_id,
        "phase_id": phase_id,
        "phase2_id": phase2_id,
        "cmo_id": cmo_id,
        "pm_id": pm_id,
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
    async def test_done_task_blocks_recreation(self, ctx: ToolContext) -> None:
        """Done tasks now also block re-creation (done-after-done prevention)."""
        tool = CreateTaskTool()
        result1 = await tool.execute({"title": "Build API"}, ctx)
        task_id = json.loads(result1)["task_id"]

        # Transition task to done
        async with ctx.db_session_factory() as db:
            task = await db.get(Task, uuid.UUID(task_id))
            task.status = TaskStatus.done
            await db.commit()

        # Should return existing done task (not create a new one)
        result2 = await tool.execute({"title": "Build API"}, ctx)
        data2 = json.loads(result2)
        assert data2["task_id"] == task_id
        assert "already exists" in data2.get("message", "")


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


# ── Issue #21: Fuzzy Assignee Matching ──────────────────────────────────


class TestCreateTaskFuzzyAssignee:
    """LLM may use variations of agent names; matching should be flexible.

    All titles use neutral text ("Do task X") to avoid keyword auto-match
    interfering with name resolution tests.
    """

    @pytest.mark.asyncio
    async def test_role_based_match(self, ctx: ToolContext) -> None:
        """'Product_Manager' should match agent with role='product_manager' (name='PM')."""
        tool = CreateTaskTool()
        result = await tool.execute({"title": "Do task alpha", "assignee": "Product_Manager"}, ctx)
        data = json.loads(result.split(" (warning")[0]) if " (warning" in result else json.loads(result)
        assert data["assigned_to"] == "PM"

    @pytest.mark.asyncio
    async def test_partial_name_match(self, ctx: ToolContext) -> None:
        """'Design' should fuzzy-match agent named 'Designer'."""
        tool = CreateTaskTool()
        result = await tool.execute({"title": "Do task beta", "assignee": "Design"}, ctx)
        data = json.loads(result.split(" (warning")[0]) if " (warning" in result else json.loads(result)
        assert data["assigned_to"] == "Designer"

    @pytest.mark.asyncio
    async def test_space_variant_match(self, ctx: ToolContext) -> None:
        """'Product Manager' (with space) should match 'PM' via role."""
        tool = CreateTaskTool()
        result = await tool.execute({"title": "Do task gamma", "assignee": "Product Manager"}, ctx)
        data = json.loads(result.split(" (warning")[0]) if " (warning" in result else json.loads(result)
        assert data["assigned_to"] == "PM"

    @pytest.mark.asyncio
    async def test_nonexistent_agent_falls_through(self, ctx: ToolContext) -> None:
        """Completely unknown name should not crash; warning issued."""
        tool = CreateTaskTool()
        result = await tool.execute({"title": "Do task delta", "assignee": "NonExistentBot"}, ctx)
        assert "not found" in result


# ── Issue #18: Race Condition Handling ──────────────────────────────────


class TestCreateTaskRaceCondition:
    """IntegrityError from DB unique index should be caught gracefully."""

    @pytest.mark.asyncio
    async def test_integrity_error_returns_existing_task(self, ctx: ToolContext, plan_env: dict) -> None:
        """When concurrent insert triggers IntegrityError, return the existing task.

        Uses a fully mocked session to avoid SQLAlchemy greenlet issues.
        Simulates: dedup query returns empty, commit raises IntegrityError,
        re-query after rollback returns the winning task.
        """
        from contextlib import asynccontextmanager
        from unittest.mock import MagicMock

        tool = CreateTaskTool()

        winner_id = uuid.uuid4()
        winner_task = MagicMock()
        winner_task.id = winner_id
        winner_task.title = "Race Task"
        winner_task.status = TaskStatus.pending

        # Build a mock session that simulates the race condition
        mock_session = AsyncMock()
        execute_count = 0

        async def mock_execute(*args, **kwargs):
            nonlocal execute_count
            execute_count += 1

            result = MagicMock()
            if execute_count == 1:
                # Phase query — return a mock phase
                phase = MagicMock()
                phase.id = plan_env["phase_id"]
                phase.status = MagicMock()
                phase.status.__eq__ = lambda s, o: True  # matches PhaseStatus.active
                phase.branch_name = "phase/1"
                result.scalars.return_value.all.return_value = [phase]
            elif execute_count == 2:
                # Dedup query — return empty (simulating race window)
                result.scalars.return_value.all.return_value = []
            elif execute_count == 3:
                # Agent query for assignee resolution
                result.scalars.return_value.all.return_value = []
            elif execute_count == 4:
                # Re-query after IntegrityError — return the winner
                result.scalar_one_or_none.return_value = winner_task
            return result

        mock_session.execute = mock_execute
        mock_session.commit = AsyncMock(side_effect=IntegrityError("duplicate key", {}, None))
        mock_session.rollback = AsyncMock()
        mock_session.add = MagicMock()

        @asynccontextmanager
        async def mock_factory():
            yield mock_session

        ctx2 = ToolContext(
            project_id=ctx.project_id,
            workspace_path=ctx.workspace_path,
            workspace_type=ctx.workspace_type,
            agent_id=ctx.agent_id,
            agent_name=ctx.agent_name,
            tenant_id=ctx.tenant_id,
            db_session_factory=mock_factory,
        )

        result = await tool.execute({"title": "Race Task"}, ctx2)
        data = json.loads(result)
        assert "already exists" in data.get("message", "")
        assert data["task_id"] == str(winner_id)
        mock_session.rollback.assert_awaited_once()


# ── Issue #2 — create_phase fuzzy dedup ───────────────────────────────


class TestCreatePhaseDeduplication:
    """create_phase_from_params must dedup near-duplicate phase names.

    Without this, CEO produces chains of nearly-identical phases like
    '시장 조사' and '시장 조사 및 아이디어 도출' that fragment the plan tree.
    """

    @pytest.fixture
    def project_setup(self):
        """Return the project_id from plan_env as the common project under test."""
        return None  # use plan_env via parameter

    @pytest.mark.asyncio
    async def test_exact_duplicate_returns_existing(
        self, test_session_maker, plan_env: dict
    ) -> None:
        # plan_env already seeds "Phase 1"
        result = await create_phase_from_params(
            name="Phase 1",
            description="dup attempt",
            project_id=plan_env["project_id"],
            db_session_factory=test_session_maker,
        )
        assert result["status"] == "already_exists"
        assert result["phase_id"] == str(plan_env["phase_id"])

    @pytest.mark.asyncio
    async def test_case_insensitive_duplicate_returns_existing(
        self, test_session_maker, plan_env: dict
    ) -> None:
        result = await create_phase_from_params(
            name="phase 1",  # mixed case
            project_id=plan_env["project_id"],
            db_session_factory=test_session_maker,
        )
        assert result["status"] == "already_exists"
        assert result["phase_id"] == str(plan_env["phase_id"])

    @pytest.mark.asyncio
    async def test_substring_phase_returns_existing(
        self, test_session_maker,
    ) -> None:
        """'시장 조사' and '시장 조사 및 아이디어 도출' collapse to the first one."""
        project_id = uuid.uuid4()
        async with test_session_maker() as session:
            session.add(Project(id=project_id, name="P", description=""))
            await session.commit()

        first = await create_phase_from_params(
            name="시장 조사",
            project_id=project_id,
            db_session_factory=test_session_maker,
        )
        assert first["status"] == "created"

        second = await create_phase_from_params(
            name="시장 조사 및 아이디어 도출",
            project_id=project_id,
            db_session_factory=test_session_maker,
        )
        assert second["status"] == "already_exists"
        assert second["phase_id"] == first["phase_id"]

    @pytest.mark.asyncio
    async def test_fuzzy_similar_phase_returns_existing(
        self, test_session_maker,
    ) -> None:
        """High similarity (word reshuffling / typos) also collapses."""
        project_id = uuid.uuid4()
        async with test_session_maker() as session:
            session.add(Project(id=project_id, name="P", description=""))
            await session.commit()

        first = await create_phase_from_params(
            name="Market Research and Ideation",
            project_id=project_id,
            db_session_factory=test_session_maker,
        )
        assert first["status"] == "created"

        second = await create_phase_from_params(
            name="Market research & ideation",  # trivial variation
            project_id=project_id,
            db_session_factory=test_session_maker,
        )
        assert second["status"] == "already_exists"
        assert second["phase_id"] == first["phase_id"]

    @pytest.mark.asyncio
    async def test_distinct_phase_is_created(
        self, test_session_maker,
    ) -> None:
        """Genuinely different names (different topic) must create a new phase."""
        project_id = uuid.uuid4()
        async with test_session_maker() as session:
            session.add(Project(id=project_id, name="P", description=""))
            await session.commit()

        first = await create_phase_from_params(
            name="시장 조사",
            project_id=project_id,
            db_session_factory=test_session_maker,
        )
        second = await create_phase_from_params(
            name="백엔드 개발",  # wholly different topic
            project_id=project_id,
            db_session_factory=test_session_maker,
        )
        assert first["status"] == "created"
        assert second["status"] == "created"
        assert first["phase_id"] != second["phase_id"]
