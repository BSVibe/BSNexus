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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.state_machine import TaskStateMachine
from backend.src.core.worker_dispatch import WorkerDispatcher
from backend.src.models import (
    PhaseStatus,
    Project,
    ProjectStatus,
    Task,
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
                await self._reassign_orphaned_tasks(db, project.id)
                await self._dispatch_agent_tasks(db, project.id)
            await db.commit()

    # ── helpers ─────────────────────────────────────────────────

    async def _list_active_projects(self, db: AsyncSession) -> list[Project]:
        result = await db.execute(
            select(Project).where(
                Project.status.in_([ProjectStatus.active, ProjectStatus.design])
            )
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

    async def _reassign_orphaned_tasks(self, db: AsyncSession, project_id: uuid.UUID) -> None:
        """Auto-assign orphaned tasks (assigned_agent_id IS NULL) via keyword matching."""
        from backend.src.core.task_assignment import match_agent_for_task
        from backend.src.models import Agent

        result = await db.execute(
            select(Task).where(
                Task.project_id == project_id,
                Task.status == TaskStatus.pending,
                Task.assigned_agent_id.is_(None),
                Task.source == "llm",
            ).order_by(Task.created_at.asc()).limit(5)
        )
        orphans = list(result.scalars().all())
        if not orphans:
            return

        agents_result = await db.execute(
            select(Agent).where(Agent.is_active.is_(True))
        )
        all_agents = list(agents_result.scalars().all())

        for task in orphans:
            text = f"{task.title} {task.description or ''}".lower()
            best = match_agent_for_task(text, all_agents, exclude_id=task.agent_id)
            if best:
                task.assigned_agent_id = best.id
                logger.info("orphan_task_assigned", task_id=str(task.id),
                            title=task.title, agent=best.name)

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


    async def _dispatch_agent_tasks(self, db: AsyncSession, project_id: uuid.UUID) -> None:
        """Find pending tasks with assigned_agent_id and dispatch agents in passive mode."""
        from backend.src.models import Agent

        result = await db.execute(
            select(Task).where(
                Task.project_id == project_id,
                Task.status == TaskStatus.pending,
                Task.assigned_agent_id.isnot(None),
            ).order_by(Task.created_at.asc()).limit(3)
        )
        pending_tasks = list(result.scalars().all())

        for task in pending_tasks:
            # Validate agent is still active
            agent_result = await db.execute(
                select(Agent).where(
                    Agent.id == task.assigned_agent_id,
                    Agent.is_active.is_(True),
                )
            )
            agent = agent_result.scalar_one_or_none()
            if not agent:
                # Agent deleted/inactive → orphan the task for reassignment
                task.assigned_agent_id = None
                logger.warning("orphaned_task", task_id=str(task.id), reason="agent_inactive")
                continue

            # Check if agent is already running a task (avoid double-dispatch)
            running_result = await db.execute(
                select(func.count(Task.id)).where(
                    Task.assigned_agent_id == task.assigned_agent_id,
                    Task.status == TaskStatus.running,
                )
            )
            if running_result.scalar_one() > 0:
                continue  # Agent is busy

            # Build task context and dispatch
            task_context = (
                f"Task ID: {task.id}\n"
                f"Title: {task.title}\n"
                f"Description: {task.description or 'No description'}\n"
                f"Priority: {task.priority.value}\n"
                f"Type: {task.task_type.value}"
            )

            # Mark task as running BEFORE dispatch to prevent double-dispatch
            sm = TaskStateMachine()
            await sm.transition(
                task=task,
                new_status=TaskStatus.running,
                reason=f"passive dispatch to {agent.name}",
                actor="dispatcher",
                db_session=db,
                stream_manager=self._stream,
            )
            task.agent_id = agent.id
            await db.flush()

            from backend.src.core.agent_queue import AgentRequest, get_agent_queue_manager
            mgr = get_agent_queue_manager()
            await mgr.enqueue(AgentRequest(
                mode="passive",
                project_id=project_id,
                agent_id=agent.id,
                tenant_id=agent.tenant_id,
                redis=self._stream.redis if self._stream else None,
                task_id=task.id,
                task_context=task_context,
            ))
            logger.info("passive_agent_dispatched", agent=agent.name, task_id=str(task.id), title=task.title)


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
