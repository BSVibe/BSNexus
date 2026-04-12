"""Plan view API — replaces the old kanban board endpoints.

Three endpoints power the new Plan view:

  GET /api/v1/projects/{id}/plan-tree         — Goal -> Phases -> Tasks tree
  GET /api/v1/projects/{id}/agent-status       — Per-agent status row
  GET /api/v1/projects/{id}/plan-tree/events  — SSE stream of plan events

Real-time updates ride on the per-project Redis Stream
``project:events:{project_id}`` (see ``RedisStreamManager``).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import structlog
from bsvibe_auth import BSVibeUser
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sse_starlette.sse import EventSourceResponse

from backend.src import models
from backend.src.core.agent_activity import (
    has_online_worker,
    resolve_agent_status_dot,
)
from backend.src.core.auth import Permission, require_permission
from backend.src.core.tenant_context import get_tenant_id
from backend.src.queue.streams import RedisStreamManager
from backend.src.storage.database import get_db

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["plan-view"])


# ── Response schemas ─────────────────────────────────────────────────


class PlanTaskNode(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    status: models.TaskStatus
    priority: models.TaskPriority
    task_type: models.TaskType
    agent_id: uuid.UUID | None = None
    agent_name: str | None = None
    depends_on_ids: list[uuid.UUID] = Field(default_factory=list)
    started_at: str | None = None
    completed_at: str | None = None


class PlanPhaseNode(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None = None
    status: models.PhaseStatus
    order: int
    tasks: list[PlanTaskNode] = Field(default_factory=list)


class PlanTreeResponse(BaseModel):
    project_id: uuid.UUID
    project_name: str
    project_status: models.ProjectStatus
    goal: str | None = None
    phases: list[PlanPhaseNode] = Field(default_factory=list)


class AgentStatusCard(BaseModel):
    agent_id: uuid.UUID
    name: str
    role: str
    title: str | None = None
    dot: str  # green | yellow | red | gray
    current_task: dict[str, Any] | None = None


# ── Helpers ──────────────────────────────────────────────────────────


def _status_dot(
    agent: models.Agent,
    current_task: models.Task | None,
    *,
    online_worker_available: bool = False,
) -> str:
    """Thin wrapper around the shared status resolver."""
    return resolve_agent_status_dot(
        agent,
        current_task=current_task,
        online_worker_available=online_worker_available,
    )


def _task_to_node(task: models.Task, agent_name: str | None) -> PlanTaskNode:
    return PlanTaskNode(
        id=task.id,
        title=task.title,
        status=task.status,
        priority=task.priority,
        task_type=task.task_type,
        agent_id=task.agent_id,
        agent_name=agent_name,
        depends_on_ids=[dep.id for dep in (task.depends_on or [])],
        started_at=task.started_at.isoformat() if task.started_at else None,
        completed_at=task.completed_at.isoformat() if task.completed_at else None,
    )


# ── Endpoints ────────────────────────────────────────────────────────


@router.get("/plan-tree", response_model=PlanTreeResponse)
async def get_plan_tree(
    project_id: uuid.UUID,
    _auth: BSVibeUser = Depends(require_permission(Permission.plan_read)),
    db: AsyncSession = Depends(get_db),
) -> PlanTreeResponse:
    """Return the full Goal -> Phase -> Task tree for a project."""
    project_result = await db.execute(
        select(models.Project)
        .where(models.Project.id == project_id)
        .options(
            selectinload(models.Project.phases)
            .selectinload(models.Phase.tasks)
            .selectinload(models.Task.depends_on),
        )
    )
    project = project_result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    # Resolve agent names referenced by tasks in one query.
    agent_ids = {t.agent_id for p in project.phases for t in p.tasks if t.agent_id is not None}
    agent_names: dict[uuid.UUID, str] = {}
    if agent_ids:
        result = await db.execute(select(models.Agent.id, models.Agent.name).where(models.Agent.id.in_(agent_ids)))
        agent_names = {row[0]: row[1] for row in result.all()}

    # Resolve goal text (top-most active goal for the project, if any).
    goal_text: str | None = None
    goal_result = await db.execute(
        select(models.Goal.title)
        .where(models.Goal.project_id == project_id, models.Goal.parent_goal_id.is_(None))
        .order_by(models.Goal.created_at.asc())
        .limit(1)
    )
    goal_row = goal_result.first()
    if goal_row is not None:
        goal_text = goal_row[0]

    phases_sorted = sorted(project.phases, key=lambda p: p.order)
    phase_nodes: list[PlanPhaseNode] = []
    for phase in phases_sorted:
        tasks_sorted = sorted(phase.tasks, key=lambda t: t.created_at)
        task_nodes = [_task_to_node(t, agent_names.get(t.agent_id) if t.agent_id else None) for t in tasks_sorted]
        phase_nodes.append(
            PlanPhaseNode(
                id=phase.id,
                name=phase.name,
                description=phase.description,
                status=phase.status,
                order=phase.order,
                tasks=task_nodes,
            )
        )

    return PlanTreeResponse(
        project_id=project.id,
        project_name=project.name,
        project_status=project.status,
        goal=goal_text,
        phases=phase_nodes,
    )


@router.get("/agent-status", response_model=list[AgentStatusCard])
async def get_agent_status(
    project_id: uuid.UUID,
    request: Request,
    _auth: BSVibeUser = Depends(require_permission(Permission.plan_read)),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> list[AgentStatusCard]:
    """Return one card per active agent in the tenant.

    The Plan view shows these at the top of the screen so users can see
    who is busy, who is idle, and what each agent is working on right now.
    """
    # Verify the project exists (404 vs empty list).
    exists = await db.execute(select(models.Project.id).where(models.Project.id == project_id))
    if exists.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Project not found")

    agents_result = await db.execute(
        select(models.Agent)
        .where(models.Agent.tenant_id == tenant_id, models.Agent.is_active.is_(True))
        .order_by(models.Agent.name.asc())
    )
    agents = list(agents_result.scalars().all())
    if not agents:
        return []

    agent_ids = [a.id for a in agents]
    running_result = await db.execute(
        select(models.Task)
        .where(
            models.Task.project_id == project_id,
            models.Task.status == models.TaskStatus.running,
            models.Task.agent_id.in_(agent_ids),
        )
    )
    running_by_agent: dict[uuid.UUID, models.Task] = {}
    for task in running_result.scalars().all():
        if task.agent_id is not None:
            running_by_agent[task.agent_id] = task

    online_worker_available = await has_online_worker(db, tenant_id)

    cards: list[AgentStatusCard] = []
    for agent in agents:
        current = running_by_agent.get(agent.id)
        cards.append(
            AgentStatusCard(
                agent_id=agent.id,
                name=agent.name,
                role=agent.role,
                title=agent.title,
                dot=resolve_agent_status_dot(
                    agent,
                    current_task=current,
                    online_worker_available=online_worker_available,
                ),
                current_task=(
                    {
                        "id": str(current.id),
                        "title": current.title,
                        "status": current.status.value,
                    }
                    if current is not None
                    else None
                ),
            )
        )
    return cards


# ── SSE event stream ─────────────────────────────────────────────────


async def _project_event_generator(
    project_id: uuid.UUID, redis: Any
) -> AsyncGenerator[dict, None]:
    stream_manager = RedisStreamManager(redis)
    stream = RedisStreamManager.project_events_stream(str(project_id))
    last_id = "$"
    while True:
        try:
            entries = await stream_manager.tail(stream, last_id=last_id, block=15000)
            for entry in entries:
                last_id = entry.pop("_message_id")
                yield {
                    "event": entry.get("event", "message"),
                    "data": json.dumps(entry.get("data", {})),
                }
        except asyncio.CancelledError:
            break
        except Exception:
            logger.warning("plan_sse_error", project_id=str(project_id), exc_info=True)
            await asyncio.sleep(1)


@router.get("/plan-tree/events")
async def plan_tree_events(
    project_id: uuid.UUID,
    request: Request,
    _auth: BSVibeUser = Depends(require_permission(Permission.plan_read)),
) -> EventSourceResponse:
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis not available")
    return EventSourceResponse(_project_event_generator(project_id, redis))
