"""VerifierWorker — Redis Stream consumer that runs Deliverable verification.

Architecture decision **A1** (locked 2026-05-08, see
``~/Docs/BSNexus/planning/decision-locks.md``).

Per-iteration loop:

1. ``XREADGROUP`` from ``verification:queue`` (consumer group ``verifier``).
2. Decode envelope, look up the Deliverable by id (tenant-scoped read).
3. Resolve a ``Verifier`` from the registry by ``verifier_type``.
4. Mark the Deliverable ``verifying`` via ``VerifierStateMachine``.
5. Call ``verifier.verify(envelope)`` — never expected to raise; if it
   does, the worker traps and synthesises a ``verification_failed``
   result so the Deliverable doesn't sit in ``verifying`` forever.
6. Apply the result via ``VerifierStateMachine.transition()``.
7. ``XACK`` the message so it doesn't redeliver.

The worker runs as a fire-and-forget asyncio task started by the FastAPI
lifespan when ``VERIFIER_ENABLED=true``. Disabling it means new
deliverables stay at ``verification_missing`` — degradable per the
project's MUST rule.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from typing import Callable

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.src.core.verifier.enqueue import (
    VERIFICATION_CONSUMER_GROUP,
    VERIFICATION_QUEUE_STREAM,
)
from backend.src.core.verifier.protocol import (
    VerificationEnvelope,
    VerificationResult,
    VerifierProofState,
)
from backend.src.core.verifier.registry import (
    VerifierNotRegisteredError,
    VerifierRegistry,
)
from backend.src.core.verifier.state_machine import (
    InvalidProofTransitionError,
    VerifierStateMachine,
    to_model_state,
)
from backend.src.models import Deliverable, ProofState
from backend.src.queue.streams import RedisStreamManager

logger = structlog.get_logger(__name__)


SessionFactory = Callable[[], AsyncSession] | async_sessionmaker[AsyncSession]


class VerifierWorker:
    """Long-lived consumer of the verification queue."""

    def __init__(
        self,
        *,
        registry: VerifierRegistry,
        stream_manager: RedisStreamManager,
        session_factory: SessionFactory,
        consumer_name: str | None = None,
        block_ms: int = 5000,
    ) -> None:
        self.registry = registry
        self.stream_manager = stream_manager
        self.session_factory = session_factory
        self.consumer_name = consumer_name or f"verifier-{uuid.uuid4().hex[:8]}"
        self.block_ms = block_ms
        self.state_machine = VerifierStateMachine()
        self._stop = asyncio.Event()

    async def ensure_consumer_group(self) -> None:
        """Create the consumer group lazily; ignore "already exists"."""
        try:
            await self.stream_manager.redis.xgroup_create(
                name=VERIFICATION_QUEUE_STREAM,
                groupname=VERIFICATION_CONSUMER_GROUP,
                id="$",
                mkstream=True,
            )
        except Exception as exc:  # noqa: BLE001 — only swallow BUSYGROUP
            if "BUSYGROUP" not in str(exc):
                logger.warning("verifier_xgroup_create_warn", error=str(exc))

    def stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        await self.ensure_consumer_group()
        logger.info(
            "verifier_worker_started",
            consumer=self.consumer_name,
            stream=VERIFICATION_QUEUE_STREAM,
            group=VERIFICATION_CONSUMER_GROUP,
        )
        while not self._stop.is_set():
            try:
                messages = await self.stream_manager.consume(
                    stream=VERIFICATION_QUEUE_STREAM,
                    group=VERIFICATION_CONSUMER_GROUP,
                    consumer=self.consumer_name,
                    count=1,
                    block=self.block_ms,
                )
            except Exception:
                logger.exception("verifier_worker_consume_error")
                await asyncio.sleep(1)
                continue

            if not messages:
                # block timeout — yield and loop. Lets shutdown take effect
                # without spinning.
                await asyncio.sleep(0)
                continue

            for raw in messages:
                message_id = raw.get("_message_id")
                try:
                    envelope = VerificationEnvelope.from_payload(raw)
                except Exception:
                    logger.exception("verifier_envelope_decode_failed", raw=raw)
                    if message_id:
                        await self.stream_manager.acknowledge(
                            VERIFICATION_QUEUE_STREAM,
                            VERIFICATION_CONSUMER_GROUP,
                            message_id,
                        )
                    continue

                await self._process_envelope(envelope)
                if message_id:
                    await self.stream_manager.acknowledge(
                        VERIFICATION_QUEUE_STREAM,
                        VERIFICATION_CONSUMER_GROUP,
                        message_id,
                    )

        logger.info("verifier_worker_stopped", consumer=self.consumer_name)

    async def _process_envelope(self, envelope: VerificationEnvelope) -> None:
        async with self._open_session() as session:
            deliverable = await self._load_deliverable(session, envelope)
            if deliverable is None:
                logger.warning(
                    "verifier_skipped_missing_deliverable",
                    deliverable_id=str(envelope.deliverable_id),
                )
                return

            try:
                verifier = self.registry.resolve(envelope.verifier_type)
            except VerifierNotRegisteredError:
                logger.error(
                    "verifier_no_handler",
                    deliverable_id=str(deliverable.id),
                    verifier_type=envelope.verifier_type.value,
                )
                # No registered handler means we cannot verify this
                # deliverable type at all. Leave the Deliverable in its
                # current state (typically ``verification_missing``) and
                # surface the gap via the proof_summary so the UI shows
                # "no verifier" instead of pretending we ran a check.
                deliverable.proof_summary = f"No verifier registered for {envelope.verifier_type.value!r}"
                await session.commit()
                return

            await self._safe_transition(session, deliverable, ProofState.verifying)
            await session.commit()

            try:
                result = await verifier.verify(envelope)
            except Exception as exc:  # noqa: BLE001 — verifier contract violation
                logger.exception(
                    "verifier_implementation_raised",
                    deliverable_id=str(deliverable.id),
                    verifier_type=envelope.verifier_type.value,
                )
                result = VerificationResult(
                    proof_state=VerifierProofState.verification_failed,
                    summary=f"Verifier raised: {exc!s}",
                )

            target_state = to_model_state(result.proof_state)
            await self._safe_transition(session, deliverable, target_state, result=result)
            await session.commit()

    async def _safe_transition(
        self,
        session: AsyncSession,
        deliverable: Deliverable,
        new_state: ProofState,
        *,
        result: VerificationResult | None = None,
    ) -> None:
        try:
            await self.state_machine.transition(
                deliverable,
                new_state,
                result=result,
                db_session=session,
                stream_manager=self.stream_manager,
            )
        except InvalidProofTransitionError:
            logger.warning(
                "verifier_state_machine_rejected",
                deliverable_id=str(deliverable.id),
                from_state=deliverable.proof_state.value,
                to_state=new_state.value,
            )

    async def _load_deliverable(
        self,
        session: AsyncSession,
        envelope: VerificationEnvelope,
    ) -> Deliverable | None:
        stmt = select(Deliverable).where(
            Deliverable.id == envelope.deliverable_id,
            Deliverable.tenant_id == envelope.tenant_id,
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    @contextlib.asynccontextmanager
    async def _open_session(self):
        factory = self.session_factory
        if isinstance(factory, async_sessionmaker):
            async with factory() as session:
                yield session
        else:
            session = factory()  # type: ignore[operator]
            if hasattr(session, "__aenter__"):
                async with session as s:
                    yield s
            else:
                try:
                    yield session
                finally:
                    await session.close()


async def start_verifier_worker_task(
    *,
    registry: VerifierRegistry,
    stream_manager: RedisStreamManager,
    session_factory: SessionFactory,
) -> tuple[VerifierWorker, asyncio.Task[None]]:
    """Spawn the worker as a fire-and-forget task. Caller should keep
    a strong reference to the returned task and call ``worker.stop()`` +
    ``await task`` during lifespan shutdown."""
    worker = VerifierWorker(
        registry=registry,
        stream_manager=stream_manager,
        session_factory=session_factory,
    )
    task: asyncio.Task[None] = asyncio.create_task(worker.run_forever())
    return worker, task
