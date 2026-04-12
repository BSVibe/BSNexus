"""Plan management tools — task, phase, goal, decision operations.

These tools replace the marker system ([CREATE_TASK], [CREATE_PHASE],
[SET_GOAL], [DECISION]). They operate via short-lived DB sessions
obtained from ``ctx.db_session_factory``.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select

from backend.src.tools.base import Tool, ToolContext, ToolExecutionError

logger = structlog.get_logger(__name__)


class CreateTaskTool(Tool):
    """Create a new task in the project. Replaces [CREATE_TASK] marker."""

    @property
    def name(self) -> str:
        return "create_task"

    @property
    def description(self) -> str:
        return (
            "Create a new task for work that needs to be done. "
            "Tasks track units of work: features, research, analysis, reviews, etc."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short task title"},
                "description": {"type": "string", "description": "Detailed description of the work"},
                "priority": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "critical"],
                    "description": "Task priority. Default: medium",
                },
                "task_type": {
                    "type": "string",
                    "enum": ["feature", "bug", "improvement", "test", "chore", "refactor"],
                    "description": "Type of work. Use chore for research/analysis.",
                },
            },
            "required": ["title"],
        }

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str:
        from backend.src.models import Phase, PhaseStatus, Task, TaskPriority, TaskSource, TaskStatus, TaskType

        title = input["title"]
        try:
            priority = TaskPriority(input.get("priority", "medium"))
        except ValueError:
            priority = TaskPriority.medium
        try:
            task_type = TaskType(input.get("task_type", "feature"))
        except ValueError:
            task_type = TaskType.feature

        async with ctx.db_session_factory() as db:
            # Find active phase or create default
            result = await db.execute(
                select(Phase)
                .where(Phase.project_id == ctx.project_id)
                .order_by(Phase.order.asc())
            )
            phases = list(result.scalars().all())
            active = next((p for p in phases if p.status == PhaseStatus.active), None)
            if not active and phases:
                active = phases[0]
            if not active:
                active = Phase(
                    project_id=ctx.project_id,
                    name="Phase 1",
                    description="Auto-created",
                    branch_name="phase/phase-1",
                    order=1,
                    status=PhaseStatus.active,
                )
                db.add(active)
                await db.flush()

            task = Task(
                project_id=ctx.project_id,
                phase_id=active.id,
                title=title,
                description=input.get("description"),
                priority=priority,
                task_type=task_type,
                source=TaskSource.llm,
                status=TaskStatus.pending,
                agent_id=ctx.agent_id,
                branch_name=active.branch_name,
            )
            db.add(task)
            await db.commit()

            logger.info("task_created_via_tool", task_id=str(task.id), title=title, agent=ctx.agent_name)
            return json.dumps({"task_id": str(task.id), "title": title, "status": "pending"})


class ClaimTaskTool(Tool):
    """Claim a pending task to work on. Transitions pending → running."""

    @property
    def name(self) -> str:
        return "claim_task"

    @property
    def description(self) -> str:
        return "Claim a pending task to start working on it. Changes status to running."

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "UUID of the task to claim"},
            },
            "required": ["task_id"],
        }

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str:
        from backend.src.models import Task, TaskStatus

        try:
            task_id = uuid.UUID(input["task_id"])
        except ValueError:
            raise ToolExecutionError(f"Invalid task_id: {input['task_id']}")

        async with ctx.db_session_factory() as db:
            result = await db.execute(
                select(Task).where(Task.id == task_id, Task.project_id == ctx.project_id)
            )
            task = result.scalar_one_or_none()
            if not task:
                raise ToolExecutionError(f"Task not found: {input['task_id']}")
            if task.status != TaskStatus.pending:
                raise ToolExecutionError(f"Task is already {task.status.value}, cannot claim")

            task.status = TaskStatus.running
            task.agent_id = ctx.agent_id
            await db.commit()

            logger.info("task_claimed_via_tool", task_id=str(task_id), agent=ctx.agent_name)
            return json.dumps({"task_id": str(task_id), "status": "running", "claimed_by": ctx.agent_name})


class CompleteTaskTool(Tool):
    """Mark a task as done with a summary."""

    @property
    def name(self) -> str:
        return "complete_task"

    @property
    def description(self) -> str:
        return "Mark a task as completed with a summary of work done and any artifacts created."

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "UUID of the task to complete"},
                "summary": {"type": "string", "description": "Summary of work done"},
                "artifacts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of file paths created or modified",
                },
            },
            "required": ["task_id", "summary"],
        }

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str:
        from backend.src.models import Task, TaskStatus

        try:
            task_id = uuid.UUID(input["task_id"])
        except ValueError:
            raise ToolExecutionError(f"Invalid task_id: {input['task_id']}")

        async with ctx.db_session_factory() as db:
            result = await db.execute(
                select(Task).where(Task.id == task_id, Task.project_id == ctx.project_id)
            )
            task = result.scalar_one_or_none()
            if not task:
                raise ToolExecutionError(f"Task not found: {input['task_id']}")
            if task.status == TaskStatus.done:
                return json.dumps({"task_id": str(task_id), "status": "already_done"})

            task.status = TaskStatus.done
            task.output_data = {
                "summary": input["summary"],
                "artifacts": input.get("artifacts", []),
                "completed_by": ctx.agent_name,
            }
            await db.commit()

            logger.info("task_completed_via_tool", task_id=str(task_id), agent=ctx.agent_name)
            return json.dumps({"task_id": str(task_id), "status": "done"})


class ListTasksTool(Tool):
    """List tasks for the current project."""

    @property
    def name(self) -> str:
        return "list_tasks"

    @property
    def description(self) -> str:
        return "List tasks in the project, optionally filtered by status. Shows task ID, title, status, and assignee."

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["pending", "running", "blocked", "done", "all"],
                    "description": "Filter by status. Default: all.",
                },
            },
        }

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str:
        from backend.src.models import Agent, Task

        status_filter = input.get("status", "all")

        async with ctx.db_session_factory() as db:
            query = select(Task).where(Task.project_id == ctx.project_id)
            if status_filter != "all":
                query = query.where(Task.status == status_filter)
            query = query.order_by(Task.created_at.asc()).limit(50)

            result = await db.execute(query)
            tasks = list(result.scalars().all())

            if not tasks:
                return f"No tasks found (filter: {status_filter})"

            lines = []
            for t in tasks:
                agent_name = ""
                if t.agent_id:
                    agent_result = await db.execute(select(Agent.name).where(Agent.id == t.agent_id))
                    agent_name = agent_result.scalar_one_or_none() or ""
                assignee = f" [{agent_name}]" if agent_name else ""
                lines.append(f"- [{t.status.value}] {t.title}{assignee} (id: {t.id})")

            return "\n".join(lines)


class CreatePhaseTool(Tool):
    """Create a lightweight phase grouping for tasks."""

    @property
    def name(self) -> str:
        return "create_phase"

    @property
    def description(self) -> str:
        return "Create a phase to group related tasks. Phases are lightweight categories, not sequential stages."

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Phase name"},
                "description": {"type": "string", "description": "What this phase covers"},
            },
            "required": ["name"],
        }

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str:
        from backend.src.models import Phase, PhaseStatus

        name = input["name"]
        async with ctx.db_session_factory() as db:
            # Dedup check
            result = await db.execute(
                select(Phase).where(Phase.project_id == ctx.project_id)
            )
            existing = list(result.scalars().all())
            for p in existing:
                if p.name.lower() == name.lower():
                    return json.dumps({"phase_id": str(p.id), "name": p.name, "status": "already_exists"})

            next_order = max((p.order for p in existing), default=0) + 1
            branch = f"phase/{name.lower().replace(' ', '-')}"

            phase = Phase(
                project_id=ctx.project_id,
                name=name,
                description=input.get("description", ""),
                branch_name=branch,
                order=next_order,
                status=PhaseStatus.pending,
            )
            db.add(phase)
            await db.commit()

            logger.info("phase_created_via_tool", phase_id=str(phase.id), name=name, agent=ctx.agent_name)
            return json.dumps({"phase_id": str(phase.id), "name": name, "status": "created"})


class SetGoalTool(Tool):
    """Set or update the project goal."""

    @property
    def name(self) -> str:
        return "set_goal"

    @property
    def description(self) -> str:
        return "Set or update the project-level goal. If a project goal already exists, it will be updated."

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Goal title"},
                "description": {"type": "string", "description": "Detailed goal description"},
            },
            "required": ["title"],
        }

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str:
        from backend.src.models import Goal

        title = input["title"]
        async with ctx.db_session_factory() as db:
            result = await db.execute(
                select(Goal).where(
                    Goal.project_id == ctx.project_id,
                    Goal.level == "project",
                ).limit(1)
            )
            existing = result.scalar_one_or_none()

            if existing:
                existing.title = title
                if input.get("description"):
                    existing.description = input["description"]
                await db.commit()
                return json.dumps({"goal_id": str(existing.id), "title": title, "status": "updated"})

            goal = Goal(
                tenant_id=ctx.tenant_id,
                project_id=ctx.project_id,
                level="project",
                title=title,
                description=input.get("description"),
            )
            db.add(goal)
            await db.commit()

            logger.info("goal_set_via_tool", goal_id=str(goal.id), title=title, agent=ctx.agent_name)
            return json.dumps({"goal_id": str(goal.id), "title": title, "status": "created"})


class RecordDecisionTool(Tool):
    """Record a project decision. Written to .bsnexus/context/decisions.md."""

    @property
    def name(self) -> str:
        return "record_decision"

    @property
    def description(self) -> str:
        return (
            "Record a confirmed project direction or decision. "
            "This is injected into all agents' prompts to prevent contradictory work."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "decision": {"type": "string", "description": "The decision text"},
            },
            "required": ["decision"],
        }

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str:
        decision = input["decision"].strip()
        if not decision:
            raise ToolExecutionError("Decision text cannot be empty")

        decisions_file = ctx.workspace_path / ".bsnexus" / "context" / "decisions.md"
        decisions_file.parent.mkdir(parents=True, exist_ok=True)

        # Read existing decisions to dedup
        existing: list[str] = []
        if decisions_file.is_file():
            for line in decisions_file.read_text().splitlines():
                stripped = line.lstrip("0123456789. ").strip()
                if stripped and not line.startswith("#"):
                    existing.append(stripped)

        if decision.lower() in (d.lower() for d in existing):
            return json.dumps({"status": "already_recorded", "decision": decision})

        existing.append(decision)
        lines = [
            "# Active Decisions\n",
            "These are confirmed project directions. Do NOT contradict them.\n",
        ]
        for i, d in enumerate(existing, 1):
            lines.append(f"{i}. {d}")
        decisions_file.write_text("\n".join(lines))

        logger.info("decision_recorded_via_tool", decision=decision[:80], agent=ctx.agent_name)
        return json.dumps({"status": "recorded", "decision": decision, "total_decisions": len(existing)})
