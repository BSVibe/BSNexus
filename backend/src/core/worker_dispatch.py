"""Worker dispatch — Redis Streams based task dispatch to remote workers."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.models.worker import Worker
from backend.src.queue.streams import RedisStreamManager

logger = structlog.get_logger(__name__)


class WorkerDispatcher:
    """Dispatches tasks to remote workers via Redis Streams.

    Protocol:
    - Each worker has a dedicated stream: tasks:worker:{worker_id}
    - Task dispatch: publish {task_id, project_id, action, title} to worker stream
    - Worker polls its stream for new tasks
    - Worker reports results to tasks:results stream
    """

    # Stream name patterns
    WORKER_STREAM_PREFIX = "tasks:worker:"
    RESULTS_STREAM = "tasks:results"

    def __init__(self, stream_manager: RedisStreamManager) -> None:
        self._stream = stream_manager

    def _worker_stream(self, worker_id: uuid.UUID) -> str:
        return f"{self.WORKER_STREAM_PREFIX}{worker_id}"

    async def dispatch_task(
        self,
        worker_id: uuid.UUID,
        task_id: uuid.UUID,
        task_title: str,
        project_id: str,
        *,
        prompt: str | None = None,
    ) -> str:
        """Publish a task to a worker's dedicated stream.

        Returns the message ID from Redis.
        """
        data: dict[str, str] = {
            "task_id": str(task_id),
            "project_id": project_id,
            "title": task_title,
            "action": "execute",
            "dispatched_at": datetime.now(timezone.utc).isoformat(),
        }
        if prompt:
            data["prompt"] = prompt

        msg_id = await self._stream.publish(self._worker_stream(worker_id), data)
        logger.info(
            "task_dispatched_to_worker",
            worker_id=str(worker_id),
            task_id=str(task_id),
            msg_id=msg_id,
        )
        return msg_id

    async def find_available_worker(
        self,
        db: AsyncSession,
        *,
        capability: str = "coding",
    ) -> Worker | None:
        """Find an online, active worker with the required capability.

        Uses JSON contains for capability matching in SQLite/PostgreSQL.
        Returns the worker with the earliest last_heartbeat (least recently used).
        """
        result = await db.execute(
            select(Worker).where(
                Worker.is_active.is_(True),
                Worker.status == "online",
            ).order_by(Worker.last_heartbeat.asc())
        )
        workers = result.scalars().all()

        for worker in workers:
            caps = worker.capabilities or []
            if capability in caps:
                return worker
        return None

    async def report_result(
        self,
        worker_id: uuid.UUID,
        task_id: uuid.UUID,
        success: bool,
        *,
        output_data: dict | None = None,
        error_message: str | None = None,
    ) -> str:
        """Report task execution result from a worker.

        Published to the results stream for the orchestrator to consume.
        """
        data: dict[str, str] = {
            "task_id": str(task_id),
            "worker_id": str(worker_id),
            "success": str(success).lower(),
            "reported_at": datetime.now(timezone.utc).isoformat(),
        }
        if output_data:
            data["output_data"] = json.dumps(output_data)
        if error_message:
            data["error_message"] = error_message

        msg_id = await self._stream.publish(self.RESULTS_STREAM, data)
        logger.info(
            "worker_result_reported",
            worker_id=str(worker_id),
            task_id=str(task_id),
            success=success,
        )
        return msg_id
