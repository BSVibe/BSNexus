"""Per-agent FIFO queue for serialized dispatch.

Both active (chat @mention) and passive (task assignment) requests go
through the same queue per agent.  A single worker coroutine per agent
processes requests sequentially, preventing the dual-dispatch problem
where an agent runs in active mode (planning tools only) while it
should be executing a task in passive mode (file_write).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class AgentRequest:
    """A queued request for an agent to process."""

    mode: Literal["active", "passive"]
    project_id: uuid.UUID
    agent_id: uuid.UUID
    tenant_id: uuid.UUID
    redis: Any
    # Active mode fields
    message: str = ""
    # Passive mode fields
    task_id: uuid.UUID | None = None
    task_context: str = ""


class AgentQueueManager:
    """Manages per-agent FIFO queues with one worker per agent.

    Usage::

        mgr = AgentQueueManager()
        await mgr.enqueue(AgentRequest(mode="active", ...))
        # Worker starts automatically on first enqueue.
        await mgr.shutdown()  # Cancel all workers on app shutdown.
    """

    def __init__(self, max_queue_size: int = 50) -> None:
        self._queues: dict[uuid.UUID, asyncio.Queue[AgentRequest]] = {}
        self._workers: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._max_queue_size = max_queue_size
        self._shutting_down = False

    async def enqueue(self, request: AgentRequest) -> None:
        """Add a request to the agent's queue. Starts worker if needed."""
        agent_id = request.agent_id
        if agent_id not in self._queues:
            self._queues[agent_id] = asyncio.Queue(maxsize=self._max_queue_size)
        if agent_id not in self._workers or self._workers[agent_id].done():
            self._workers[agent_id] = asyncio.create_task(
                self._process_queue(agent_id),
                name=f"agent-queue-{agent_id}",
            )
        try:
            self._queues[agent_id].put_nowait(request)
            logger.info(
                "agent_queue_enqueued",
                agent_id=str(agent_id),
                mode=request.mode,
                queue_size=self._queues[agent_id].qsize(),
            )
        except asyncio.QueueFull:
            logger.warning(
                "agent_queue_full",
                agent_id=str(agent_id),
                mode=request.mode,
                max_size=self._max_queue_size,
            )

    async def _process_queue(self, agent_id: uuid.UUID) -> None:
        """Single worker per agent — processes requests sequentially."""
        queue = self._queues[agent_id]
        while not self._shutting_down:
            try:
                request = await asyncio.wait_for(queue.get(), timeout=60.0)
            except asyncio.TimeoutError:
                # No requests for 60s — exit worker (will restart on next enqueue)
                if queue.empty():
                    logger.debug("agent_queue_worker_idle_exit", agent_id=str(agent_id))
                    break
                continue
            except asyncio.CancelledError:
                break

            try:
                if request.mode == "active":
                    await self._execute_active(request)
                else:
                    await self._execute_passive(request)
            except Exception as e:
                logger.error(
                    "agent_queue_execution_error",
                    agent_id=str(agent_id),
                    mode=request.mode,
                    error=str(e),
                )
            finally:
                queue.task_done()

    async def _execute_active(self, req: AgentRequest) -> None:
        """Run an active-mode agent request."""
        from backend.src.api.agent_chat import _process_agent_in_background

        await _process_agent_in_background(
            project_id=req.project_id,
            agent_id=req.agent_id,
            user_message=req.message,
            redis=req.redis,
            tenant_id=req.tenant_id,
        )

    async def _execute_passive(self, req: AgentRequest) -> None:
        """Run a passive-mode agent request."""
        from backend.src.api.agent_chat import _process_agent_in_background_passive

        assert req.task_id is not None
        await _process_agent_in_background_passive(
            project_id=req.project_id,
            agent_id=req.agent_id,
            task_id=req.task_id,
            task_context=req.task_context,
            redis=req.redis,
            tenant_id=req.tenant_id,
        )

    async def cancel_project(self, project_id: uuid.UUID) -> int:
        """Cancel all queued/running work for a project.

        Drains queues of matching requests and cancels worker tasks
        that are currently executing a request for the project.
        Returns count of cancelled items.
        """
        cancelled = 0
        for agent_id, queue in list(self._queues.items()):
            # Drain matching requests from queue
            drained: list[AgentRequest] = []
            while not queue.empty():
                try:
                    req = queue.get_nowait()
                    if req.project_id == project_id:
                        cancelled += 1
                        queue.task_done()
                    else:
                        drained.append(req)
                except asyncio.QueueEmpty:
                    break
            # Re-enqueue non-matching requests
            for req in drained:
                try:
                    queue.put_nowait(req)
                except asyncio.QueueFull:
                    pass

        # Cancel worker tasks (they'll restart on next enqueue)
        for agent_id, worker in list(self._workers.items()):
            if not worker.done():
                worker.cancel()
                cancelled += 1

        logger.info("agent_queue_project_cancelled", project_id=str(project_id), cancelled=cancelled)
        return cancelled

    async def shutdown(self) -> None:
        """Cancel all worker tasks."""
        self._shutting_down = True
        for task in self._workers.values():
            if not task.done():
                task.cancel()
        if self._workers:
            await asyncio.gather(*self._workers.values(), return_exceptions=True)
        self._workers.clear()
        self._queues.clear()
        logger.info("agent_queue_manager_shutdown")


# Module-level singleton — initialized in app lifespan.
_manager: AgentQueueManager | None = None


def get_agent_queue_manager() -> AgentQueueManager:
    """Get the singleton AgentQueueManager. Raises if not initialized."""
    if _manager is None:
        raise RuntimeError("AgentQueueManager not initialized. Call init_agent_queue_manager() first.")
    return _manager


def init_agent_queue_manager() -> AgentQueueManager:
    """Create and set the singleton."""
    global _manager
    _manager = AgentQueueManager()
    return _manager


async def shutdown_agent_queue_manager() -> None:
    """Shutdown the singleton."""
    global _manager
    if _manager is not None:
        await _manager.shutdown()
        _manager = None
