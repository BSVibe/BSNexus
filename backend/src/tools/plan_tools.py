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
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from backend.src.tools.base import Tool, ToolContext, ToolExecutionError

logger = structlog.get_logger(__name__)


def _normalize_name(name: str) -> str:
    """Normalize agent name for flexible matching: lowercase, strip, replace _ with space."""
    return name.strip().lower().replace("_", " ")


def _resolve_agent_by_name(name: str, agents: list) -> "Any | None":
    """Resolve an agent by name with flexible matching.

    Matching priority:
    1. Exact name match (case-insensitive)
    2. Role match (e.g. "Product_Manager" matches role="product_manager")
    3. Starts-with match (e.g. "Design" matches "Designer")
    """
    normalized = _normalize_name(name)

    # 1. Exact name match
    for a in agents:
        if _normalize_name(a.name) == normalized:
            return a

    # 2. Role match (underscores/spaces normalized)
    for a in agents:
        if _normalize_name(a.role) == normalized:
            return a

    # 3. Starts-with match (e.g. "Design" → "Designer")
    for a in agents:
        a_name = _normalize_name(a.name)
        if a_name.startswith(normalized) or normalized.startswith(a_name):
            return a

    return None


async def create_task_from_params(
    *,
    title: str,
    description: str | None = None,
    priority: str = "medium",
    task_type: str = "feature",
    assignee: str | None = None,
    phase_name: str | None = None,
    project_id: uuid.UUID,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    agent_name: str,
    db_session_factory: Any,
    tasks_created_count: int = 0,
    max_tasks: int = 10,
) -> dict[str, Any]:
    """Create a task with full dedup, fuzzy assignee matching, and race guard.

    Returns dict with ``task_id``, ``title``, ``status``, ``assigned_to``,
    and optionally ``message`` / ``warning``.

    Raises ``ToolExecutionError`` on validation failure.
    """
    from backend.src.models import Agent, Phase, PhaseStatus, Task, TaskPriority, TaskSource, TaskStatus, TaskType

    if tasks_created_count >= max_tasks:
        raise ToolExecutionError(
            f"Maximum {max_tasks} tasks per turn. Focus on the most important tasks."
        )

    try:
        task_priority = TaskPriority(priority)
    except ValueError:
        task_priority = TaskPriority.medium
    try:
        task_type_enum = TaskType(task_type)
    except ValueError:
        task_type_enum = TaskType.feature

    async with db_session_factory() as db:
        # Find target phase: explicit phase_name → active phase → first phase.
        result = await db.execute(
            select(Phase)
            .where(Phase.project_id == project_id)
            .order_by(Phase.order.asc())
        )
        phases = list(result.scalars().all())

        target_phase: Phase | None = None
        fallback_reason: str | None = None
        if phase_name:
            target_phase = _match_phase_by_name(phase_name, phases)
            if target_phase is None:
                fallback_reason = "no_fuzzy_match"
        if not target_phase:
            target_phase = next((p for p in phases if p.status == PhaseStatus.active), None)
            if target_phase and fallback_reason:
                logger.warning(
                    "phase_resolution_fallback",
                    requested=phase_name,
                    resolved_to=target_phase.name,
                    resolved_status=target_phase.status.value,
                    task_title=title,
                    reason=fallback_reason,
                )
        if not target_phase and phases:
            target_phase = phases[0]
            if fallback_reason:
                logger.warning(
                    "phase_resolution_fallback",
                    requested=phase_name,
                    resolved_to=target_phase.name,
                    resolved_status=target_phase.status.value,
                    task_title=title,
                    reason=f"{fallback_reason}_no_active",
                )
        if not target_phase:
            raise ToolExecutionError(
                "No phase exists yet. Use create_phase first to organize work, then create tasks."
            )
        active = target_phase

        # ── Duplicate check (phase-scoped, includes done tasks) ──
        from difflib import SequenceMatcher

        existing_result = await db.execute(
            select(Task).where(Task.phase_id == active.id)
        )
        existing_tasks = list(existing_result.scalars().all())
        normalized_title = title.strip().lower()

        # Exact match (case-insensitive) → return existing task
        for et in existing_tasks:
            if et.title.strip().lower() == normalized_title:
                return {
                    "task_id": str(et.id), "title": et.title,
                    "status": et.status.value, "assigned_to": None,
                    "message": f"Task already exists in this phase ({et.status.value}) — skipping creation.",
                }

        # Fuzzy match (>0.8 similarity) → error with suggestion
        similar = [
            et.title for et in existing_tasks
            if SequenceMatcher(None, normalized_title, et.title.strip().lower()).ratio() > 0.8
        ]
        if similar:
            titles_list = ", ".join(f'"{t}"' for t in similar)
            raise ToolExecutionError(
                f"Very similar task(s) already exist: {titles_list}. "
                "Use the existing task or choose a clearly different title."
            )

        # Resolve assignee: explicit name → keyword auto-match
        assigned_agent_id = None
        assignee_resolved = assignee
        warning = ""

        # Load all active agents for matching
        all_agents_result = await db.execute(
            select(Agent).where(
                Agent.tenant_id == tenant_id,
                Agent.is_active.is_(True),
            )
        )
        all_agents = list(all_agents_result.scalars().all())

        if assignee_resolved:
            # Self-assign guard
            if assignee_resolved.strip().lower() == agent_name.strip().lower():
                warning = " (warning: self-assignment blocked — delegate to others)"
                assignee_resolved = None
            else:
                agent_match = _resolve_agent_by_name(assignee_resolved, all_agents)
                if agent_match:
                    assigned_agent_id = agent_match.id
                    assignee_resolved = agent_match.name
                else:
                    warning = f" (warning: agent '{assignee_resolved}' not found)"

        if not assigned_agent_id:
            from backend.src.core.task_assignment import match_agent_for_task
            text = f"{title} {description or ''}".lower()
            best = match_agent_for_task(text, all_agents, exclude_id=agent_id)
            if best:
                assigned_agent_id = best.id
                assignee_resolved = best.name

        task = Task(
            project_id=project_id,
            phase_id=active.id,
            title=title,
            description=description,
            priority=task_priority,
            task_type=task_type_enum,
            source=TaskSource.llm,
            status=TaskStatus.pending,
            creator_agent_id=agent_id,
            assigned_agent_id=assigned_agent_id,
            branch_name=active.branch_name,
        )
        db.add(task)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            existing_result = await db.execute(
                select(Task).where(
                    Task.phase_id == active.id,
                    Task.status != TaskStatus.done,
                    func.lower(func.trim(Task.title)) == normalized_title,
                )
            )
            existing = existing_result.scalar_one_or_none()
            if existing:
                logger.info("task_dedup_race_caught", title=title, winner_id=str(existing.id))
                return {
                    "task_id": str(existing.id), "title": existing.title,
                    "status": existing.status.value, "assigned_to": None,
                    "message": "Task already exists in this phase — skipping creation.",
                }
            raise

        logger.info("task_created", task_id=str(task.id), title=title,
                    agent=agent_name, assignee=assignee_resolved)
        result_dict: dict[str, Any] = {
            "task_id": str(task.id), "title": title, "status": "pending",
            "assigned_to": assignee_resolved or None,
        }
        if warning:
            result_dict["warning"] = warning
        return result_dict


def _normalize_phase_name(name: str) -> str:
    """Strip punctuation, collapse whitespace, lowercase.

    Keeps Hangul (가-힣) and word chars so Korean phase names normalize correctly.
    ``"Market research & ideation"`` → ``"market research ideation"``.
    ``"시장 조사 및 아이디어 도출"`` → ``"시장 조사 및 아이디어 도출"``.
    """
    import re

    cleaned = re.sub(r"[^\w가-힣]+", " ", name, flags=re.UNICODE)
    return re.sub(r"\s+", " ", cleaned).strip().lower()


def _match_phase_by_name(requested: str, phases: list) -> "Any | None":
    """Return the phase that most closely matches ``requested``, or None.

    Matching ladder — first hit wins:
      - exact case-insensitive match on ``name``
      - normalized exact match (punctuation / whitespace collapsed)
      - substring containment (when both sides have ≥ 4 normalized chars)
      - ``SequenceMatcher`` ratio ≥ 0.75 on the normalized forms

    Pure function — shared between phase dedup and ``create_task_from_params``
    phase resolution so both behave identically. Accepts any object with a
    ``name`` attribute (tests pass simple stand-ins).
    """
    from difflib import SequenceMatcher

    if not requested:
        return None
    norm_req = _normalize_phase_name(requested)
    if not norm_req:
        return None

    for p in phases:
        if p.name.lower() == requested.lower():
            return p
        norm_ex = _normalize_phase_name(p.name)
        if not norm_ex:
            continue
        if norm_req == norm_ex:
            return p
        if len(norm_req) >= 4 and len(norm_ex) >= 4 and (
            norm_req in norm_ex or norm_ex in norm_req
        ):
            return p
        # SequenceMatcher only fires on reasonably long names. Short generic
        # names (e.g. "Phase 1" vs "Phase 2") produce a 0.85+ ratio from
        # a single char difference and would collapse false positives.
        if len(norm_req) >= 8 and len(norm_ex) >= 8 and (
            SequenceMatcher(None, norm_req, norm_ex).ratio() >= 0.75
        ):
            return p
    return None


# Backwards-compat alias — existing phase-dedup call sites expect this name.
_find_duplicate_phase = _match_phase_by_name


async def create_phase_from_params(
    *,
    name: str,
    description: str = "",
    project_id: uuid.UUID,
    db_session_factory: Any,
) -> dict[str, Any]:
    """Create a phase with exact + fuzzy dedup guard.

    Returns dict with ``phase_id``, ``name``, ``status``.
    """
    from backend.src.models import Phase, PhaseStatus

    async with db_session_factory() as db:
        result = await db.execute(
            select(Phase).where(Phase.project_id == project_id)
        )
        existing = list(result.scalars().all())
        dup = _find_duplicate_phase(name, existing)
        if dup is not None:
            logger.info(
                "phase_dedup_match",
                requested=name,
                matched=dup.name,
                phase_id=str(dup.id),
            )
            return {"phase_id": str(dup.id), "name": dup.name, "status": "already_exists"}

        next_order = max((p.order for p in existing), default=0) + 1
        branch = f"phase/{name.lower().replace(' ', '-')}"
        has_active = any(p.status == PhaseStatus.active for p in existing)
        initial_status = PhaseStatus.active if not existing or not has_active else PhaseStatus.pending

        phase = Phase(
            project_id=project_id,
            name=name,
            description=description,
            branch_name=branch,
            order=next_order,
            status=initial_status,
        )
        db.add(phase)
        await db.commit()

        logger.info("phase_created", phase_id=str(phase.id), name=name)
        return {"phase_id": str(phase.id), "name": name, "status": "created"}


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
        result = await create_task_from_params(
            title=input["title"],
            description=input.get("description"),
            priority=input.get("priority", "medium"),
            task_type=input.get("task_type", "feature"),
            assignee=input.get("assignee"),
            phase_name=input.get("phase_name"),
            project_id=ctx.project_id,
            tenant_id=ctx.tenant_id,
            agent_id=ctx.agent_id,
            agent_name=ctx.agent_name,
            db_session_factory=ctx.db_session_factory,
            tasks_created_count=ctx.tasks_created_this_turn,
            max_tasks=ctx.max_tasks_per_turn,
        )
        # Only increment counter for actual new creations
        if "message" not in result:
            ctx.tasks_created_this_turn += 1
        warning = result.pop("warning", "")
        return json.dumps(result) + warning


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
                if t.creator_agent_id:
                    agent_result = await db.execute(select(Agent.name).where(Agent.id == t.creator_agent_id))
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
        result = await create_phase_from_params(
            name=input["name"],
            description=input.get("description", ""),
            project_id=ctx.project_id,
            db_session_factory=ctx.db_session_factory,
        )
        return json.dumps(result)


async def upsert_project_goal(
    *,
    db_session_factory: Any,
    project_id: uuid.UUID,
    tenant_id: uuid.UUID,
    title: str,
    description: str | None,
) -> dict[str, str]:
    """Insert-or-update the `level="project"` Goal for a project.

    Shared by `SetGoalTool` (passive mode) and the inline `[SET_GOAL]`
    block marker handler (active mode) so both paths end in the same
    DB state — one `Goal(level="project")` row per project.
    """
    from backend.src.models import Goal

    async with db_session_factory() as db:
        result = await db.execute(
            select(Goal).where(
                Goal.project_id == project_id,
                Goal.level == "project",
            ).limit(1)
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.title = title
            if description:
                existing.description = description
            await db.commit()
            return {"goal_id": str(existing.id), "title": title, "status": "updated"}

        goal = Goal(
            tenant_id=tenant_id,
            project_id=project_id,
            level="project",
            title=title,
            description=description,
        )
        db.add(goal)
        await db.commit()
        return {"goal_id": str(goal.id), "title": title, "status": "created"}


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
        title = input["title"]
        result = await upsert_project_goal(
            db_session_factory=ctx.db_session_factory,
            project_id=ctx.project_id,
            tenant_id=ctx.tenant_id,
            title=title,
            description=input.get("description"),
        )
        if result["status"] == "created":
            logger.info(
                "goal_set_via_tool", goal_id=result["goal_id"], title=title,
                agent=ctx.agent_name,
            )
        return json.dumps(result)


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
