"""Deterministic VerifierWorker (G6.1).

Consumes ``proof:queue`` Redis Stream entries that the
``/api/v1/deliverables/{id}/verify`` route publishes, runs the
deterministic verifier (``run_proof_attempt``), stamps the
``ProofAttempt`` + ``Deliverable.proof_state``, and fans the
``deliverable_proof`` event onto the project SSE stream so open
BSNexus tabs refresh.

Queue contract:
  Stream:           ``proof:queue``
  Consumer group:   ``proof-worker``
  Message payload:  ``{deliverable_id: uuid, tenant_id: uuid}``

The worker is started from the FastAPI lifespan
(``backend.src.queue.background.start_background_consumer``).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.src.core.domain import ProofState
from backend.src.core.git_ops import CommitOpError, commit_deliverable
from backend.src.core.git_ops.branch import GithubClientFactory
from backend.src.core.proof import run_proof_attempt
from backend.src.models import Deliverable, Project
from backend.src.queue.streams import RedisStreamManager

logger = structlog.get_logger(__name__)

PROOF_QUEUE_STREAM = "proof:queue"
PROOF_QUEUE_GROUP = "proof-worker"

PublishEvent = Callable[[str, str, dict], Awaitable[None]]


async def process_one(
    *,
    deliverable_id: uuid.UUID,
    tenant_id: uuid.UUID,
    session: AsyncSession,
    publish_event: PublishEvent | None,
    github_client_factory: GithubClientFactory | None = None,
) -> None:
    """Run the deterministic verifier against a single Deliverable.

    Raises ``LookupError`` if the deliverable doesn't exist in the
    given tenant scope (a cross-tenant message must not leak verifier
    capacity). The caller's job is to ack-then-skip in that case so a
    poisoned message doesn't pin a slot.

    G8.2 — when the proof state transitions to ``verified`` and the
    project has a repo binding, the deliverable's artifact files are
    committed to ``bsnexus/req-<id>`` via the GitHub Contents API and
    ``deliverable.commit_sha`` is stamped. Commit failures NEVER
    revert ``verified`` — they're soft warnings (logged + surfaced as
    ``commit_sha=None``) so the proof signal stays canonical.
    """
    deliverable = await _load_scoped(session, deliverable_id, tenant_id)
    project = await _load_project(session, deliverable.project_id)

    workspace_root = project.workspace_dir or ""
    if not workspace_root or not Path(workspace_root).exists():
        # No workspace on disk → no deterministic policy can run.
        # Mark human_review_required via the verifier's no-policy branch
        # by passing a phantom root; ``run_proof_attempt`` resolves to
        # ``no_policy`` when neither pyproject nor package.json is
        # present.
        workspace_root = workspace_root or "/tmp"

    await run_proof_attempt(
        deliverable=deliverable,
        workspace_root=workspace_root,
        session=session,
        changed_files=tuple(_artifact_paths(deliverable)),
    )

    if (
        deliverable.proof_state == ProofState.verified
        and project.github_repo_url
        and project.github_token_encrypted
        and deliverable.request_id is not None
    ):
        try:
            result = await commit_deliverable(
                deliverable=deliverable,
                session=session,
                client_factory=github_client_factory,
            )
            await session.flush()
            logger.info(
                "verifier_worker_committed",
                deliverable_id=str(deliverable.id),
                branch=result.branch_name,
                commit_sha=result.commit_sha,
            )
        except CommitOpError as exc:
            # Soft failure: keep the verified state, leave commit_sha None.
            logger.warning(
                "verifier_worker_commit_skipped",
                deliverable_id=str(deliverable.id),
                reason=exc.reason,
                message=str(exc),
            )

    # G9 — back-half orchestration. Walk the WorkStep + Request state
    # machines forward now that this Deliverable's proof has resolved;
    # a fully-verified Request reaches ``shipped`` here, which fires
    # the G8.3 PR hook. Soft by contract — an orchestration hiccup
    # must never revert the verified proof we just stamped.
    # Imported lazily: ``orchestration`` → ``run_attempt_executor`` →
    # ``verifier`` is a module cycle; the deferred import breaks it.
    from backend.src.core.orchestration import advance_request_after_proof  # noqa: PLC0415

    try:
        await advance_request_after_proof(
            deliverable=deliverable,
            session=session,
            stream_manager=_stream_manager_from_publish(publish_event),
        )
    except Exception:
        logger.exception(
            "verifier_worker_advance_failed",
            deliverable_id=str(deliverable.id),
        )

    if publish_event is not None:
        await publish_event(
            str(deliverable.project_id),
            "deliverable_proof",
            {
                "id": str(deliverable.id),
                "project_id": str(deliverable.project_id),
                "proof_state": deliverable.proof_state.value,
                "commit_sha": deliverable.commit_sha,
            },
        )
    logger.info(
        "verifier_worker_processed",
        deliverable_id=str(deliverable.id),
        project_id=str(deliverable.project_id),
        proof_state=deliverable.proof_state.value,
    )


def _stream_manager_from_publish(publish_event: PublishEvent | None) -> object:
    """``advance_request_after_proof`` only needs a stream manager to
    pass through to ``transition_request``'s G8.3 PR hook, which in
    turn just needs *something* truthy when a repo is bound. The
    VerifierWorker hands ``publish_event`` (a bound method of the
    RedisStreamManager) — recover the manager off ``__self__`` when
    present; otherwise ``None`` (the M0 bridge path, no Redis).
    """
    return getattr(publish_event, "__self__", None)


async def _load_scoped(session: AsyncSession, deliverable_id: uuid.UUID, tenant_id: uuid.UUID) -> Deliverable:
    stmt = select(Deliverable).where(Deliverable.id == deliverable_id, Deliverable.tenant_id == tenant_id)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise LookupError(f"Deliverable {deliverable_id} not in tenant scope {tenant_id}")
    return row


async def _load_project(session: AsyncSession, project_id: uuid.UUID) -> Project:
    row = await session.get(Project, project_id)
    if row is None:
        raise LookupError(f"Project {project_id} not found")
    return row


def _artifact_paths(deliverable: Deliverable) -> list[str]:
    refs = deliverable.artifact_refs or []
    paths: list[str] = []
    for ref in refs:
        if isinstance(ref, dict):
            path = ref.get("path")
            if isinstance(path, str):
                paths.append(path)
        elif isinstance(ref, str):
            paths.append(ref)
    return paths


class VerifierWorker:
    """Long-running consumer that drives ``process_one`` against each
    proof:queue message.

    Created and started from the FastAPI lifespan. ``stop()`` is
    cooperative — the loop checks the ``_stopping`` flag between
    blocks. A pending ``run_proof_attempt`` call is allowed to
    complete; that's why ``stop()`` awaits ``self._task``.
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
        self._consumer_name = consumer_name or f"proof-worker-{uuid.uuid4().hex[:8]}"
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    async def start(self) -> None:
        await self._ensure_group()
        self._stopping = False
        self._task = asyncio.create_task(self._run_loop())
        logger.info("verifier_worker_started", consumer=self._consumer_name)

    async def stop(self) -> None:
        self._stopping = True
        task = self._task
        if task is None:
            return
        # ``consume`` blocks on Redis for up to 30s; cancel forces the
        # loop to wake up.
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001 — log later
            pass
        self._task = None
        logger.info("verifier_worker_stopped", consumer=self._consumer_name)

    async def _ensure_group(self) -> None:
        """Create the consumer group + the stream if either is missing."""
        try:
            await self._stream_manager.redis.xgroup_create(
                name=PROOF_QUEUE_STREAM,
                groupname=PROOF_QUEUE_GROUP,
                id="$",
                mkstream=True,
            )
        except Exception as exc:  # noqa: BLE001 — redis-py raises BUSYGROUP
            if "BUSYGROUP" not in str(exc):
                raise

    async def _run_loop(self) -> None:
        while not self._stopping:
            try:
                messages = await self._stream_manager.consume(
                    stream=PROOF_QUEUE_STREAM,
                    group=PROOF_QUEUE_GROUP,
                    consumer=self._consumer_name,
                    count=1,
                    block=30_000,
                )
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("verifier_worker_consume_failed")
                # Backoff briefly so a misconfigured Redis doesn't spin
                # the loop at 100% CPU.
                await asyncio.sleep(1)
                continue

            for message in messages:
                await self._handle_message(message)

    async def _handle_message(self, message: dict) -> None:
        message_id = message.get("_message_id")
        try:
            deliverable_id = uuid.UUID(str(message["deliverable_id"]))
            tenant_id = uuid.UUID(str(message["tenant_id"]))
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("verifier_worker_bad_message", error=str(exc), message=message)
            if message_id:
                await self._stream_manager.acknowledge(PROOF_QUEUE_STREAM, PROOF_QUEUE_GROUP, message_id)
            return

        async with self._session_factory() as session:
            try:
                await process_one(
                    deliverable_id=deliverable_id,
                    tenant_id=tenant_id,
                    session=session,
                    publish_event=self._stream_manager.publish_project_event,
                )
            except LookupError:
                logger.warning(
                    "verifier_worker_cross_tenant",
                    deliverable_id=str(deliverable_id),
                    tenant_id=str(tenant_id),
                )
            except Exception:
                logger.exception(
                    "verifier_worker_process_failed",
                    deliverable_id=str(deliverable_id),
                )
                # Leave message unacked — Redis Streams' pending
                # entries list (PEL) makes it retriable. Operator can
                # XCLAIM/XAUTOCLAIM later.
                return

        if message_id:
            await self._stream_manager.acknowledge(PROOF_QUEUE_STREAM, PROOF_QUEUE_GROUP, message_id)


__all__ = [
    "PROOF_QUEUE_GROUP",
    "PROOF_QUEUE_STREAM",
    "VerifierWorker",
    "process_one",
]


# stdlib `logging` is needed by structlog's stdlib bridge during tests;
# keep an explicit import-site reference so static analysers (and the
# Python runtime) don't drop it.
_ = logging
