"""RequestWorker (G9) — production orchestrator front half.

Consumes ``request:queue`` and drives each freshly-created Request
through ``plan_and_dispatch_request`` (WorkPlan → WorkStep →
``dispatch_run_attempt`` → Deliverable enqueued on ``proof:queue``).

Modelled 1:1 on ``VerifierWorker``: same start/stop/run-loop shape,
same consumer-group + PEL-retry semantics, started from the FastAPI
lifespan. The two workers form the autonomous loop —

    Direction → request:queue → RequestWorker → proof:queue
              → VerifierWorker → (G8.2 commit, G9 finalize) → shipped → PR

Queue contract:
  Stream:          ``request:queue``
  Consumer group:  ``request-worker``
  Message payload: ``{request_id: uuid, tenant_id: uuid}``
"""

from __future__ import annotations

import asyncio
import logging
import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.src.core.orchestration import (
    RE_ENGAGE_KIND,
    plan_and_dispatch_request,
    re_dispatch_decision,
)
from backend.src.queue.streams import RedisStreamManager

logger = structlog.get_logger(__name__)

REQUEST_QUEUE_STREAM = "request:queue"
REQUEST_QUEUE_GROUP = "request-worker"


async def process_one(
    *,
    request_id: uuid.UUID,
    tenant_id: uuid.UUID,
    session: AsyncSession,
    stream_manager: RedisStreamManager,
    kind: str | None = None,
    decision_id: uuid.UUID | None = None,
) -> None:
    """Drive one queued ``request:queue`` message.

    Two message kinds share the stream:
      - default (no ``kind``) → ``plan_and_dispatch_request``: a
        freshly-created Request from the Direction chat.
      - ``kind=re_engage`` → ``re_dispatch_decision``: a founder
        resolved a blocking Decision; the stalled WorkStep is
        re-dispatched (``retry`` / ``reframe``).

    Raises ``LookupError`` for a cross-tenant id so the caller can
    ack-then-skip a poisoned message. Idempotent — a re-delivered
    message for a Request that is no longer in the expected state is a
    safe no-op.
    """
    if kind == RE_ENGAGE_KIND:
        if decision_id is None:
            raise ValueError("re_engage message missing decision_id")
        await re_dispatch_decision(
            decision_id=decision_id,
            tenant_id=tenant_id,
            session=session,
            stream_manager=stream_manager,
        )
        return
    await plan_and_dispatch_request(
        request_id=request_id,
        tenant_id=tenant_id,
        session=session,
        stream_manager=stream_manager,
    )


class RequestWorker:
    """Long-running consumer that drives ``process_one`` against each
    ``request:queue`` message.

    Created and started from the FastAPI lifespan. ``stop()`` is
    cooperative — the loop checks ``_stopping`` between blocks; a
    pending ``plan_and_dispatch_request`` (which can span a multi-round
    LLM tool loop) is allowed to finish, which is why ``stop()`` awaits
    the task.
    """

    def __init__(
        self,
        *,
        stream_manager: RedisStreamManager,
        session_factory: async_sessionmaker[AsyncSession],
        consumer_name: str | None = None,
    ) -> None:
        self._stream_manager = stream_manager
        self._session_factory = session_factory
        self._consumer_name = consumer_name or f"request-worker-{uuid.uuid4().hex[:8]}"
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    async def start(self) -> None:
        await self._ensure_group()
        self._stopping = False
        self._task = asyncio.create_task(self._run_loop())
        logger.info("request_worker_started", consumer=self._consumer_name)

    async def stop(self) -> None:
        self._stopping = True
        task = self._task
        if task is None:
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001 — best-effort drain
            pass
        self._task = None
        logger.info("request_worker_stopped", consumer=self._consumer_name)

    async def _ensure_group(self) -> None:
        """Create the consumer group + the stream if either is missing."""
        try:
            await self._stream_manager.redis.xgroup_create(
                name=REQUEST_QUEUE_STREAM,
                groupname=REQUEST_QUEUE_GROUP,
                id="$",
                mkstream=True,
            )
        except Exception as exc:  # noqa: BLE001 — redis-py raises BUSYGROUP as a bare error
            if "BUSYGROUP" not in str(exc):
                raise

    async def _run_loop(self) -> None:
        while not self._stopping:
            try:
                messages = await self._stream_manager.consume(
                    stream=REQUEST_QUEUE_STREAM,
                    group=REQUEST_QUEUE_GROUP,
                    consumer=self._consumer_name,
                    count=1,
                    block=30_000,
                )
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("request_worker_consume_failed")
                await asyncio.sleep(1)
                continue

            for message in messages:
                await self._handle_message(message)

    async def _handle_message(self, message: dict) -> None:
        message_id = message.get("_message_id")
        kind = message.get("kind")
        try:
            request_id = uuid.UUID(str(message["request_id"]))
            tenant_id = uuid.UUID(str(message["tenant_id"]))
            decision_id = uuid.UUID(str(message["decision_id"])) if message.get("decision_id") is not None else None
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("request_worker_bad_message", error=str(exc), message=message)
            if message_id:
                await self._stream_manager.acknowledge(REQUEST_QUEUE_STREAM, REQUEST_QUEUE_GROUP, message_id)
            return

        async with self._session_factory() as session:
            try:
                await process_one(
                    request_id=request_id,
                    tenant_id=tenant_id,
                    session=session,
                    stream_manager=self._stream_manager,
                    kind=kind,
                    decision_id=decision_id,
                )
            except LookupError:
                logger.warning(
                    "request_worker_cross_tenant",
                    request_id=str(request_id),
                    tenant_id=str(tenant_id),
                )
            except Exception:
                logger.exception(
                    "request_worker_process_failed",
                    request_id=str(request_id),
                )
                # Leave the message unacked — Redis Streams' PEL makes
                # it retriable; an operator can XAUTOCLAIM later.
                return

        if message_id:
            await self._stream_manager.acknowledge(REQUEST_QUEUE_STREAM, REQUEST_QUEUE_GROUP, message_id)


__all__ = [
    "REQUEST_QUEUE_GROUP",
    "REQUEST_QUEUE_STREAM",
    "RequestWorker",
    "process_one",
]


# structlog's stdlib bridge needs `logging` importable during tests;
# keep an explicit reference so static analysers don't drop it.
_ = logging
