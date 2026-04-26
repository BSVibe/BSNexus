"""Background consumer that drains ``runs:results`` → finalises runs.

A remote worker reports the outcome of a run via
``POST /api/v1/workers/result``, which publishes onto
``runs:results``. The backend itself stays out of the worker's
request/response cycle, so *something* has to translate those stream
messages into orchestrator transitions. That's this consumer.

One long-running asyncio.Task per backend process. For each message:

  success=true  → RunOrchestrator.on_run_completed (pending → done).
  success=false → RunStateMachine.transition to `blocked`.

Always acknowledges the stream entry so a poisonous message never
loops forever; the run row keeps the error message for inspection.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

# P0.7 — Audit run.post for LLM/worker runs is now the responsibility
# of BSGateway's LiteLLM async_post_call_hook (Lockin §Architectural
# shifts #1). The worker-result consumer no longer resolves an
# AuditSink — the run.post BSupervisor event was already sent by
# BSGateway upstream of the worker-result publish.
from backend.src.core.integrations import get_tenant_integration_snapshot
from backend.src.core.run_artifacts import publish_run_output
from backend.src.core.run_orchestrator import get_run_orchestrator
from backend.src.core.state_machine import RunStateMachine
from backend.src.models import ExecutionRun, RunStatus
from backend.src.queue.streams import RedisStreamManager

logger = structlog.get_logger(__name__)

RESULTS_STREAM = "runs:results"
RESULTS_GROUP = "run-orchestrator"
CONSUMER_NAME = "orchestrator-main"


class WorkerResultConsumer:
    """Drains ``runs:results`` and finalises the referenced run."""

    def __init__(
        self,
        *,
        stream_manager: RedisStreamManager,
        session_maker: async_sessionmaker,
        block_ms: int = 5000,
        batch_size: int = 5,
    ):
        self._stream = stream_manager
        self._session_maker = session_maker
        self._block_ms = block_ms
        self._batch_size = batch_size
        self._state = RunStateMachine()
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        try:
            await self._stream.redis.xgroup_create(RESULTS_STREAM, RESULTS_GROUP, id="0", mkstream=True)
        except Exception as exc:  # noqa: BLE001
            if "BUSYGROUP" not in str(exc):
                logger.warning("worker_result_consumer_xgroup_create_failed", error=str(exc))
        self._task = asyncio.create_task(self._run())
        logger.info("worker_result_consumer_started")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                messages = await self._stream.consume(
                    RESULTS_STREAM,
                    RESULTS_GROUP,
                    CONSUMER_NAME,
                    count=self._batch_size,
                    block=self._block_ms,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("worker_result_consume_failed", error=str(exc))
                await asyncio.sleep(1.0)
                continue

            if not messages:
                # Empty-result path — yield so cancellation can land.
                await asyncio.sleep(0)
                continue

            for msg in messages:
                try:
                    await self._handle_message(msg)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "worker_result_handle_failed",
                        message_id=msg.get("_message_id"),
                    )
                finally:
                    mid = msg.get("_message_id")
                    if mid:
                        try:
                            await self._stream.acknowledge(RESULTS_STREAM, RESULTS_GROUP, mid)
                        except Exception:  # noqa: BLE001
                            logger.warning("worker_result_ack_failed", message_id=mid)

    async def _handle_message(self, msg: dict[str, Any]) -> None:
        raw_run_id = msg.get("run_id")
        if not raw_run_id:
            logger.warning("worker_result_missing_run_id", msg=msg)
            return
        try:
            run_id = uuid.UUID(raw_run_id)
        except (ValueError, TypeError):
            logger.warning("worker_result_bad_run_id", run_id=raw_run_id)
            return

        success = str(msg.get("success", "")).lower() == "true"
        error_message = msg.get("error_message") or None
        output_data = msg.get("output_data")
        if isinstance(output_data, str):
            try:
                output_data = json.loads(output_data)
            except (json.JSONDecodeError, TypeError):
                output_data = {"raw": output_data}

        async with self._session_maker() as session:
            run = (await session.execute(select(ExecutionRun).where(ExecutionRun.id == run_id))).scalar_one_or_none()
            if run is None:
                logger.warning("worker_result_unknown_run", run_id=str(run_id))
                return
            if run.status != RunStatus.running:
                logger.info(
                    "worker_result_ignored_non_running_run",
                    run_id=str(run_id),
                    status=run.status.value,
                )
                return

            if not success:
                await self._state.transition(
                    run,
                    RunStatus.blocked,
                    reason=error_message or "worker reported failure",
                    actor="worker-result-consumer",
                    db_session=session,
                    stream_manager=self._stream,
                )
                await session.commit()
                return

            # Build the orchestrator-shaped result dict + finalize.
            result = {
                "status": "done",
                "output_type": "text",
                "output_ref": output_data or {"inline": ""},
                "actual_cost_cents": int(msg.get("actual_cost_cents") or 0),
            }
            snapshot = await get_tenant_integration_snapshot(session, run.tenant_id)
            await get_run_orchestrator().on_run_completed(
                run,
                result=result,
                db=session,
                stream_manager=self._stream,
            )
            # Surface the worker's output on the founder-facing UI —
            # assistant-role chat reply + Deliverable row — and index
            # the result into BSage when configured. Idempotent so a
            # duplicate stream delivery is a no-op.
            from backend.src.core.composer import resolve_knowledge_client
            from backend.src.models import Request as _Request

            originator_token: str | None = None
            if run.request_id is not None:
                req_row = (
                    await session.execute(select(_Request).where(_Request.id == run.request_id))
                ).scalar_one_or_none()
                if req_row is not None:
                    originator_token = req_row.originator_auth
            knowledge = resolve_knowledge_client(snapshot.bsage, auth_token=originator_token)
            await publish_run_output(run, session, knowledge=knowledge)
            await session.commit()
