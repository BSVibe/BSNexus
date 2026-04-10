"""Global background dispatcher loop.

A single asyncio task started in the FastAPI lifespan that periodically:

1. Promotes pending tasks whose dependencies are met into running state by
   handing them to an available worker.
2. Advances phases: when every task in the active phase is done, marks
   the phase complete and activates the next pending phase.
3. Publishes plan SSE events so the frontend updates without polling.

This replaces the old per-project PMOrchestrator. There is one loop per
process, not one per project — the chat path stays fire-and-forget and
this loop catches everything else (manual task creation, server restart,
worker reconnect, dependency promotion).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.state_machine import TaskStateMachine
from backend.src.core.worker_dispatch import WorkerDispatcher
from backend.src.models import (
    PhaseStatus,
    Project,
    ProjectStatus,
    TaskStatus,
)
from backend.src.queue.streams import RedisStreamManager
from backend.src.repositories.phase_repository import PhaseRepository
from backend.src.repositories.task_repository import TaskRepository
from backend.src.storage.database import async_session

logger = structlog.get_logger(__name__)

DISPATCH_INTERVAL_SECONDS = 5.0


class GlobalDispatcher:
    """Single-instance background dispatcher.

    The instance lives on ``app.state.global_dispatcher`` and is started /
    stopped from the FastAPI lifespan.
    """

    def __init__(self, stream_manager: RedisStreamManager) -> None:
        self._stream = stream_manager
        self._state_machine = TaskStateMachine()
        self._worker_dispatcher = WorkerDispatcher(stream_manager)
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    # ── lifecycle ────────────────────────────────────────────────

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="global-dispatcher")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is None:
            return
        try:
            await asyncio.wait_for(self._task, timeout=5.0)
        except asyncio.TimeoutError:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, BaseException):  # noqa: BLE001
                pass
        self._task = None

    # ── main loop ────────────────────────────────────────────────

    async def _run(self) -> None:
        logger.info("global_dispatcher_started", interval=DISPATCH_INTERVAL_SECONDS)
        while not self._stop_event.is_set():
            try:
                await self.tick()
            except Exception:  # noqa: BLE001
                logger.exception("global_dispatcher_tick_failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=DISPATCH_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                continue
        logger.info("global_dispatcher_stopped")

    async def tick(self) -> None:
        """One iteration of the dispatcher loop. Public for tests."""
        async with async_session() as db:
            active_projects = await self._list_active_projects(db)
            for project in active_projects:
                await self._advance_phase_if_complete(db, project.id)
                await self._promote_and_dispatch(db, project.id)
            await db.commit()

    # ── helpers ─────────────────────────────────────────────────

    async def _list_active_projects(self, db: AsyncSession) -> list[Project]:
        result = await db.execute(
            select(Project).where(Project.status == ProjectStatus.active)
        )
        return list(result.scalars().all())

    async def _promote_and_dispatch(self, db: AsyncSession, project_id: uuid.UUID) -> None:
        """Find pending tasks in the active phase and hand them to workers."""
        phase_repo = PhaseRepository(db)
        active_phase = await phase_repo.get_active_phase(project_id)
        if active_phase is None:
            return

        task_repo = TaskRepository(db)
        pending_tasks = await task_repo.list_ready_by_priority(project_id)
        for task in pending_tasks:
            if task.phase_id != active_phase.id:
                continue
            if not await task_repo.check_dependencies_met(task.id):
                continue

            worker = await self._worker_dispatcher.find_available_worker(db)
            if worker is None:
                # No capacity right now — try again next tick.
                return

            try:
                await self._state_machine.transition(
                    task=task,
                    new_status=TaskStatus.running,
                    reason="dispatched by global dispatcher",
                    actor="dispatcher",
                    db_session=db,
                    stream_manager=self._stream,
                )
            except ValueError:
                # Already running or otherwise not eligible — skip.
                continue

            await self._worker_dispatcher.dispatch_task(
                worker_id=worker.id,
                task_id=task.id,
                task_title=task.title,
                project_id=str(project_id),
                prompt=_extract_prompt(task.worker_prompt),
            )

    async def _advance_phase_if_complete(self, db: AsyncSession, project_id: uuid.UUID) -> None:
        """If every task in the active phase is done, activate the next pending phase."""
        phase_repo = PhaseRepository(db)
        active_phase = await phase_repo.get_active_phase(project_id)
        if active_phase is None:
            return

        incomplete = await phase_repo.count_incomplete_tasks(active_phase.id)
        if incomplete > 0:
            return

        # Active phase is complete. Mark it done and activate the next one.
        active_phase.status = PhaseStatus.completed
        next_phase = await phase_repo.get_next_pending_phase(project_id, active_phase.order)
        if next_phase is not None:
            next_phase.status = PhaseStatus.active

        await self._stream.publish_project_event(
            str(project_id),
            "phase_advanced",
            {
                "completed_phase_id": str(active_phase.id),
                "next_phase_id": str(next_phase.id) if next_phase is not None else None,
            },
        )


def _extract_prompt(worker_prompt: dict | None) -> str | None:
    if not worker_prompt:
        return None
    if isinstance(worker_prompt, dict):
        prompt = worker_prompt.get("prompt")
        if isinstance(prompt, str):
            return prompt
    return None


# ── lifespan helpers ─────────────────────────────────────────────


async def start_global_dispatcher(app: Any) -> GlobalDispatcher:
    stream_manager: RedisStreamManager = app.state.stream_manager
    dispatcher = GlobalDispatcher(stream_manager)
    dispatcher.start()
    app.state.global_dispatcher = dispatcher
    return dispatcher


async def stop_global_dispatcher(app: Any) -> None:
    dispatcher: GlobalDispatcher | None = getattr(app.state, "global_dispatcher", None)
    if dispatcher is not None:
        await dispatcher.stop()
        app.state.global_dispatcher = None
