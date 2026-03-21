from __future__ import annotations

import asyncio
import logging
import uuid

from backend.src import models
from backend.src.config import settings
from backend.src.core.executor import create_executor
from backend.src.core.orchestrator import PMOrchestrator
from backend.src.core.state_machine import TaskStateMachine
from backend.src.core.task_runner import LocalTaskRunner
from backend.src.repositories.phase_repository import PhaseRepository
from backend.src.repositories.task_repository import TaskRepository
from backend.src.storage.database import async_session, get_db
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/pm", tags=["pm"])


# -- Dependency helpers --------------------------------------------------------


def _get_stream_manager(request: Request) -> object:
    """Get the RedisStreamManager from app state."""
    return request.app.state.stream_manager


def _ensure_orchestrators(request: Request) -> dict[str, dict]:
    """Ensure the orchestrators dict exists on app.state and return it."""
    if not hasattr(request.app.state, "orchestrators"):
        request.app.state.orchestrators = {}
    return request.app.state.orchestrators


def _build_orchestrator(request: Request) -> PMOrchestrator:
    """Build a PMOrchestrator with LocalTaskRunner."""
    stream_manager = _get_stream_manager(request)
    return PMOrchestrator(
        stream_manager=stream_manager,
        task_runner=LocalTaskRunner(create_executor(settings.executor_type)),
        state_machine=TaskStateMachine(),
    )


# -- Endpoints ----------------------------------------------------------------


@router.post("/{project_id}/start")
async def start_orchestration(
    project_id: uuid.UUID,
    request: Request,
) -> dict:
    """Start orchestration for a project."""
    orchestrators = _ensure_orchestrators(request)
    pid = str(project_id)

    if pid in orchestrators and orchestrators[pid].get("running"):
        raise HTTPException(status_code=409, detail="Orchestrator already running for this project")

    orchestrator = _build_orchestrator(request)

    task = asyncio.create_task(orchestrator.start(project_id, async_session))

    entry = {
        "orchestrator": orchestrator,
        "task": task,
        "running": True,
    }
    orchestrators[pid] = entry

    def _on_orchestrator_done(fut: asyncio.Task[None]) -> None:
        entry["running"] = False
        if fut.cancelled():
            logger.warning("Orchestrator task cancelled for project %s", pid)
        elif fut.exception() is not None:
            logger.error("Orchestrator task crashed for project %s: %s", pid, fut.exception(), exc_info=fut.exception())

    task.add_done_callback(_on_orchestrator_done)

    return {"detail": "Orchestration started", "project_id": pid}


@router.post("/{project_id}/pause")
async def pause_orchestration(
    project_id: uuid.UUID,
    request: Request,
) -> dict:
    """Pause orchestration for a project."""
    orchestrators = _ensure_orchestrators(request)
    pid = str(project_id)

    entry = orchestrators.get(pid)
    if not entry or not entry.get("running"):
        raise HTTPException(status_code=404, detail="No running orchestrator for this project")

    orchestrator: PMOrchestrator = entry["orchestrator"]
    await orchestrator.stop()
    entry["running"] = False

    return {"detail": "Orchestration paused", "project_id": pid}


@router.get("/{project_id}/status")
async def get_orchestration_status(
    project_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get orchestration status for a project."""
    orchestrators = _ensure_orchestrators(request)
    pid = str(project_id)

    entry = orchestrators.get(pid)
    running = bool(entry and entry.get("running"))

    # Task counts by status
    repo = TaskRepository(db)
    task_counts = await repo.count_by_status(project_id)

    return {
        "project_id": pid,
        "running": running,
        "tasks": task_counts,
    }


@router.post("/{project_id}/promote-waiting")
async def promote_waiting_tasks(
    project_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Promote WAITING tasks in the active phase with all dependencies met to READY."""
    phase_repo = PhaseRepository(db)
    task_repo = TaskRepository(db)
    state_machine = TaskStateMachine()

    active_phase = await phase_repo.get_active_phase(project_id)
    if active_phase is None:
        return {"detail": "No active phase", "promoted": []}

    waiting_tasks = await task_repo.list_waiting_in_phase(active_phase.id)

    promoted: list[dict] = []
    for task in waiting_tasks:
        if await task_repo.check_dependencies_met(task.id):
            await state_machine.transition(
                task=task,
                new_status=models.TaskStatus.ready,
                reason="All dependencies met",
                actor="system",
                db_session=db,
                stream_manager=request.app.state.stream_manager
                if hasattr(request.app.state, "stream_manager")
                else None,
            )
            promoted.append({"task_id": str(task.id), "title": task.title})

    await db.commit()
    return {"detail": f"Promoted {len(promoted)} tasks to ready", "promoted": promoted}


@router.post("/{project_id}/queue-next")
async def queue_next_task(
    project_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Manually move the highest-priority READY task to in_progress."""
    orchestrator = _build_orchestrator(request)

    task = await orchestrator.queue_next(project_id, db)
    if not task:
        raise HTTPException(status_code=404, detail="No ready tasks to queue")

    await db.commit()

    return {
        "detail": "Task started",
        "task_id": str(task.id),
        "title": task.title,
        "priority": task.priority.value,
    }
