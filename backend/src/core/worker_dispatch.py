"""Worker dispatch — Redis Streams based run dispatch to remote workers."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.models.worker import Worker
from backend.src.queue.streams import RedisStreamManager

logger = structlog.get_logger(__name__)


class WorkerDispatcher:
    """Dispatches runs to remote workers via Redis Streams.

    Protocol:
    - Each worker has a dedicated stream: ``runs:worker:{worker_id}``
    - Run dispatch: publish ``{run_id, project_id, action, prompt, …}``
    - Worker polls its stream for new runs
    - Worker reports results to ``runs:results``
    """

    WORKER_STREAM_PREFIX = "runs:worker:"
    RESULTS_STREAM = "runs:results"

    def __init__(self, stream_manager: RedisStreamManager) -> None:
        self._stream = stream_manager

    def _worker_stream(self, worker_id: uuid.UUID) -> str:
        return f"{self.WORKER_STREAM_PREFIX}{worker_id}"

    async def dispatch_run(
        self,
        worker_id: uuid.UUID,
        run_id: uuid.UUID,
        project_id: str,
        *,
        system_prompt: str,
        user_prompt: str = "",
        tools_allowed: list[str] | None = None,
        workspace_dir: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        """Publish a run to a worker's dedicated stream.

        The CLI used to run the LLM is fixed at the worker's
        registration / startup (``bsnexus-worker run --executor …`` or
        auto-detect). Backend routing picks a matching worker via
        ``required_capabilities`` in ``find_available_worker``; once
        chosen, the worker runs whatever CLI it was configured with.
        """
        data: dict[str, str] = {
            "run_id": str(run_id),
            "project_id": project_id,
            "action": "execute",
            "system_prompt": system_prompt,
            "dispatched_at": datetime.now(timezone.utc).isoformat(),
        }
        if user_prompt:
            data["user_prompt"] = user_prompt
        if tools_allowed:
            data["tools_allowed"] = json.dumps(tools_allowed)
        if workspace_dir:
            data["workspace_dir"] = workspace_dir
        if history:
            data["history"] = json.dumps(history)

        msg_id = await self._stream.publish(self._worker_stream(worker_id), data)
        logger.info(
            "run_dispatched_to_worker",
            worker_id=str(worker_id),
            run_id=str(run_id),
            msg_id=msg_id,
        )
        return msg_id

    async def find_available_worker(
        self,
        db: AsyncSession,
        tenant_id: uuid.UUID | None = None,
        required_capabilities: list[str] | None = None,
    ) -> Worker | None:
        """Find an online, active worker scoped to the tenant (LRU).

        When ``required_capabilities`` is non-empty, the returned worker
        must expose *every* listed capability in its ``capabilities``
        JSON array. Capability filtering runs in Python (portable across
        PostgreSQL + SQLite in tests); the candidate pool is small, so
        the cost is negligible.
        """
        heartbeat_cutoff = datetime.now(timezone.utc) - timedelta(seconds=120)
        query = select(Worker).where(
            Worker.is_active.is_(True),
            Worker.status == "online",
            Worker.last_heartbeat > heartbeat_cutoff,
        )
        if tenant_id is not None:
            query = query.where(Worker.tenant_id == tenant_id)
        result = await db.execute(query.order_by(Worker.last_heartbeat.asc()))
        candidates = list(result.scalars())
        if required_capabilities:
            required = set(required_capabilities)
            candidates = [
                w for w in candidates if required.issubset(set(w.capabilities or []))
            ]
        return candidates[0] if candidates else None

    async def report_result(
        self,
        worker_id: uuid.UUID,
        run_id: uuid.UUID,
        success: bool,
        *,
        output_ref: dict | None = None,
        output_type: str | None = None,
        actual_cost_cents: int | None = None,
        error_message: str | None = None,
    ) -> str:
        """Report run execution result from a worker."""
        data: dict[str, str] = {
            "run_id": str(run_id),
            "worker_id": str(worker_id),
            "success": str(success).lower(),
            "reported_at": datetime.now(timezone.utc).isoformat(),
        }
        if output_ref is not None:
            data["output_ref"] = json.dumps(output_ref)
        if output_type:
            data["output_type"] = output_type
        if actual_cost_cents is not None:
            data["actual_cost_cents"] = str(actual_cost_cents)
        if error_message:
            data["error_message"] = error_message

        msg_id = await self._stream.publish(self.RESULTS_STREAM, data)
        logger.info(
            "worker_result_reported",
            worker_id=str(worker_id),
            run_id=str(run_id),
            success=success,
        )
        return msg_id
