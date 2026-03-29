"""MCP-compatible API endpoints for external tool integration.

Provides a JSON-RPC style interface that MCP clients can consume:
list projects, read board state, create/update tasks, query dependencies,
and trigger execution.
"""

from __future__ import annotations

import uuid
from collections import deque

import structlog
from bsvibe_auth import BSVibeUser
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src import models, schemas
from backend.src.core.auth import Permission, require_permission
from backend.src.core.state_machine import TaskStateMachine
from backend.src.repositories.phase_repository import PhaseRepository
from backend.src.repositories.project_repository import ProjectRepository
from backend.src.repositories.task_repository import TaskRepository
from backend.src.storage.database import get_db

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/mcp", tags=["mcp"])

state_machine = TaskStateMachine()


# ── Request / Response schemas ────────────────────────────────────────


class MCPCreateTaskRequest(BaseModel):
    project_id: uuid.UUID
    title: str = Field(..., min_length=1, max_length=500)
    description: str = ""
    task_type: schemas.TaskType = schemas.TaskType.feature


class MCPUpdateStatusRequest(BaseModel):
    status: schemas.TaskStatus


class MCPTriggerExecutorRequest(BaseModel):
    pass  # no body needed


class MCPDependencyNode(BaseModel):
    task_id: uuid.UUID
    title: str
    status: str
    depends_on: list[uuid.UUID] = Field(default_factory=list)


class MCPDependencyGraphResponse(BaseModel):
    task_id: uuid.UUID
    nodes: list[MCPDependencyNode]


# ── Helpers ───────────────────────────────────────────────────────────


def _task_summary(task: models.Task) -> dict:
    """Build a minimal task dict for MCP responses."""
    dep_ids = [dep.id for dep in task.depends_on] if task.depends_on else []
    return {
        "id": str(task.id),
        "project_id": str(task.project_id),
        "phase_id": str(task.phase_id),
        "title": task.title,
        "description": task.description,
        "status": task.status.value,
        "priority": task.priority.value,
        "task_type": task.task_type.value,
        "depends_on": [str(d) for d in dep_ids],
    }


# ── Endpoints ─────────────────────────────────────────────────────────


@router.get("/projects")
async def list_projects(
    _auth: BSVibeUser = Depends(require_permission(Permission.project_read)),
    db: AsyncSession = Depends(get_db),
) -> list[schemas.ProjectResponse]:
    """List all projects."""
    repo = ProjectRepository(db)
    projects = await repo.list_all(limit=200, offset=0)
    return [schemas.ProjectResponse.model_validate(p) for p in projects]


@router.get("/board/{project_id}")
async def get_board_state(
    project_id: uuid.UUID,
    _auth: BSVibeUser = Depends(require_permission(Permission.board_read)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get kanban board state for a project."""
    repo = TaskRepository(db)
    tasks = await repo.list_by_project(project_id, limit=500)

    kanban_statuses = [s for s in models.TaskStatus if s != models.TaskStatus.redesign]
    columns: dict[str, list[dict]] = {status.value: [] for status in kanban_statuses}
    redesign_tasks: list[dict] = []

    for task in tasks:
        summary = _task_summary(task)
        if task.status == models.TaskStatus.redesign:
            redesign_tasks.append(summary)
        else:
            columns[task.status.value].append(summary)

    status_counts = await repo.count_by_status(project_id)

    return {
        "project_id": str(project_id),
        "columns": columns,
        "stats": status_counts,
        "redesign_tasks": redesign_tasks,
    }


@router.post("/tasks", status_code=201)
async def create_task(
    body: MCPCreateTaskRequest,
    _auth: BSVibeUser = Depends(require_permission(Permission.task_create)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Create a new task in the first active phase of the given project."""
    project_repo = ProjectRepository(db)
    project = await project_repo.get_by_id(body.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    phase_repo = PhaseRepository(db)
    active_phase = await phase_repo.get_active_phase(body.project_id)
    if active_phase is None:
        # Fall back to first pending phase
        active_phase = await phase_repo.get_first_pending_phase(body.project_id)
    if active_phase is None:
        raise HTTPException(status_code=400, detail="Project has no available phase")

    task = models.Task(
        project_id=body.project_id,
        phase_id=active_phase.id,
        title=body.title,
        description=body.description,
        priority=models.TaskPriority.medium,
        task_type=models.TaskType(body.task_type.value),
        source=models.TaskSource.manual,
        status=(
            models.TaskStatus.ready if active_phase.status == models.PhaseStatus.active else models.TaskStatus.waiting
        ),
        worker_prompt={"prompt": body.description},
        qa_prompt={"prompt": f"Verify that: {body.title}"},
        version=1,
    )
    db.add(task)
    await db.flush()
    await db.commit()

    task_repo = TaskRepository(db)
    created = await task_repo.get_by_id(task.id)
    if created is None:
        raise HTTPException(status_code=500, detail="Task creation failed")
    return _task_summary(created)


@router.patch("/tasks/{task_id}/status")
async def update_task_status(
    task_id: uuid.UUID,
    body: MCPUpdateStatusRequest,
    request: Request,
    _auth: BSVibeUser = Depends(require_permission(Permission.task_transition)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Update a task's status via state machine transition."""
    repo = TaskRepository(db)
    task = await repo.get_by_id(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    model_status = models.TaskStatus(body.status.value)
    stream_manager = getattr(getattr(request.app, "state", None), "stream_manager", None)

    try:
        await state_machine.transition(
            task=task,
            new_status=model_status,
            reason="MCP status update",
            actor="mcp",
            db_session=db,
            stream_manager=stream_manager,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    await db.commit()

    task = await repo.get_by_id(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return _task_summary(task)


@router.get("/tasks/{task_id}/dependencies")
async def get_task_dependencies(
    task_id: uuid.UUID,
    _auth: BSVibeUser = Depends(require_permission(Permission.task_read)),
    db: AsyncSession = Depends(get_db),
) -> MCPDependencyGraphResponse:
    """Get dependency graph for a task (direct + transitive)."""
    repo = TaskRepository(db)
    root_task = await repo.get_by_id(task_id, load_depends=True)
    if root_task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    # BFS to collect transitive dependencies
    visited: set[uuid.UUID] = set()
    nodes: list[MCPDependencyNode] = []
    bfs_queue: deque[models.Task] = deque([root_task])

    while bfs_queue:
        current = bfs_queue.popleft()
        if current.id in visited:
            continue
        visited.add(current.id)

        dep_ids = [dep.id for dep in current.depends_on] if current.depends_on else []
        nodes.append(
            MCPDependencyNode(
                task_id=current.id,
                title=current.title,
                status=current.status.value,
                depends_on=dep_ids,
            )
        )

        for dep in current.depends_on or []:
            if dep.id not in visited:
                dep_task = await repo.get_by_id(dep.id, load_depends=True)
                if dep_task:
                    bfs_queue.append(dep_task)

    return MCPDependencyGraphResponse(task_id=task_id, nodes=nodes)


@router.post("/tasks/{task_id}/execute")
async def trigger_executor(
    task_id: uuid.UUID,
    request: Request,
    _auth: BSVibeUser = Depends(require_permission(Permission.pm_control)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Trigger execution for a task by transitioning it to ready.

    The PM orchestrator's execution loop will pick it up automatically.
    """
    repo = TaskRepository(db)
    task = await repo.get_by_id(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.status not in (models.TaskStatus.waiting, models.TaskStatus.redesign):
        raise HTTPException(
            status_code=400,
            detail=f"Task cannot be triggered from status '{task.status.value}'. Must be 'waiting' or 'redesign'.",
        )

    stream_manager = getattr(getattr(request.app, "state", None), "stream_manager", None)

    if task.status == models.TaskStatus.redesign:
        # redesign -> waiting (valid transition), then waiting -> ready
        task.retry_count = 0
        task.error_message = None
        await state_machine.transition(
            task=task,
            new_status=models.TaskStatus.waiting,
            reason="Reset via MCP trigger",
            actor="mcp",
            db_session=db,
            stream_manager=stream_manager,
        )
        await state_machine.transition(
            task=task,
            new_status=models.TaskStatus.ready,
            reason="Triggered via MCP",
            actor="mcp",
            db_session=db,
            stream_manager=stream_manager,
        )
    else:
        # waiting -> ready
        await state_machine.transition(
            task=task,
            new_status=models.TaskStatus.ready,
            reason="Triggered via MCP",
            actor="mcp",
            db_session=db,
            stream_manager=stream_manager,
        )
    await db.commit()

    task = await repo.get_by_id(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"detail": "Task queued for execution", **_task_summary(task)}
