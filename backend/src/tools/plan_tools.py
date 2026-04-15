"""Plan management tools — task, phase, goal, decision operations.

These tools replace the marker system ([CREATE_TASK], [CREATE_PHASE],
[SET_GOAL], [DECISION]). They operate via short-lived DB sessions
obtained from ``ctx.db_session_factory``.
"""

from __future__ import annotations

import json
import uuid
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
                "assignee": {
                    "type": "string",
                    "description": "Name of the agent to assign this task to (e.g. 'CTO', 'Designer'). "
                                   "The agent will be automatically dispatched to work on it.",
                },
                "phase_name": {
                    "type": "string",
                    "description": "Name of the phase to add this task to. "
                                   "If omitted, the task goes to the active phase.",
                },
            },
            "required": ["title"],
        }

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str:
        from backend.src.models import Agent, Phase, PhaseStatus, Task, TaskPriority, TaskSource, TaskStatus, TaskType

        # Rate limit: prevent infinite task creation loops
        if ctx.tasks_created_this_turn >= ctx.max_tasks_per_turn:
            raise ToolExecutionError(
                f"Maximum {ctx.max_tasks_per_turn} tasks per turn. "
                "Focus on the most important tasks."
            )

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
            # Find target phase: explicit phase_name → active phase → first phase.
            result = await db.execute(
                select(Phase)
                .where(Phase.project_id == ctx.project_id)
                .order_by(Phase.order.asc())
            )
            phases = list(result.scalars().all())

            target_phase: Phase | None = None
            phase_name_input = input.get("phase_name")
            if phase_name_input:
                target_phase = next(
                    (p for p in phases if p.name.lower() == phase_name_input.lower()), None
                )
            if not target_phase:
                target_phase = next((p for p in phases if p.status == PhaseStatus.active), None)
            if not target_phase and phases:
                target_phase = phases[0]
            if not target_phase:
                raise ToolExecutionError(
                    "No phase exists yet. Use create_phase first to organize work, then create tasks."
                )
            active = target_phase

            # Resolve assignee: explicit name → keyword auto-match
            assigned_agent_id = None
            assignee_name = input.get("assignee")
            assignee_warning = ""

            # Load all active agents for matching
            all_agents_result = await db.execute(
                select(Agent).where(
                    Agent.tenant_id == ctx.tenant_id,
                    Agent.is_active.is_(True),
                )
            )
            all_agents = list(all_agents_result.scalars().all())

            if assignee_name:
                # Explicit assignee by name
                assignee = next(
                    (a for a in all_agents if a.name.lower() == assignee_name.strip().lower()),
                    None,
                )
                if assignee:
                    assigned_agent_id = assignee.id
                else:
                    assignee_warning = f" (warning: agent '{assignee_name}' not found)"

            if not assigned_agent_id:
                # Auto-assign by keyword matching on title + description
                from backend.src.core.task_assignment import match_agent_for_task
                text = f"{title} {input.get('description', '')}".lower()
                best = match_agent_for_task(text, all_agents, exclude_id=ctx.agent_id)
                if best:
                    assigned_agent_id = best.id
                    assignee_name = best.name

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
                assigned_agent_id=assigned_agent_id,
                branch_name=active.branch_name,
            )
            db.add(task)
            await db.commit()

            ctx.tasks_created_this_turn += 1
            logger.info("task_created_via_tool", task_id=str(task.id), title=title,
                        agent=ctx.agent_name, assignee=assignee_name)
            return json.dumps({
                "task_id": str(task.id), "title": title, "status": "pending",
                "assigned_to": assignee_name or None,
            }) + assignee_warning


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
        from backend.src.core.state_machine import TaskStateMachine
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
            # Allow idempotent claim: if already running and assigned to this agent,
            # the dispatcher pre-transitioned it — just succeed.
            if task.status == TaskStatus.running and task.assigned_agent_id == ctx.agent_id:
                return f"Task '{task.title}' already claimed and running."
            if task.status != TaskStatus.pending:
                raise ToolExecutionError(f"Task is already {task.status.value}, cannot claim")

            task.agent_id = ctx.agent_id
            sm = TaskStateMachine()
            await sm.transition(
                task, TaskStatus.running,
                actor=f"agent:{ctx.agent_id}",
                reason=f"Claimed by {ctx.agent_name}",
                db_session=db,
                stream_manager=ctx.stream_manager,
            )
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
        from backend.src.core.state_machine import TaskStateMachine
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

            task.output_data = {
                "summary": input["summary"],
                "artifacts": input.get("artifacts", []),
                "completed_by": ctx.agent_name,
            }
            sm = TaskStateMachine()
            await sm.transition(
                task, TaskStatus.done,
                actor=f"agent:{ctx.agent_id}",
                reason=input["summary"],
                db_session=db,
                stream_manager=ctx.stream_manager,
            )
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

            # First phase in a project auto-activates. Subsequent phases stay pending
            # until the dispatcher advances them on completion of the active phase.
            has_active = any(p.status == PhaseStatus.active for p in existing)
            initial_status = PhaseStatus.active if not existing or not has_active else PhaseStatus.pending

            phase = Phase(
                project_id=ctx.project_id,
                name=name,
                description=input.get("description", ""),
                branch_name=branch,
                order=next_order,
                status=initial_status,
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
