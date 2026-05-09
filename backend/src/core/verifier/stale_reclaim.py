"""Auto-reclaim stale ``verification_missing`` deliverables.

Defense-in-depth for the verifier chain. The PR8 race fix moved
enqueue post-commit, but other transaction-visibility / queue-drop
patterns may still produce a deliverable that:

- has ``verifier_type`` stamped (LLM emitted a fenced block ⇒ the
  parser ran ⇒ enqueue was attempted), AND
- sits at ``proof_state=verification_missing`` past the worker's
  normal processing window

→ likely a dropped enqueue or worker-crash mid-process. Re-enqueue.

Capped at ``max_retries`` per deliverable via
``verifier_inputs.retry_count`` so we don't loop forever on a
deliverable the worker keeps failing to verify (those should land at
``verification_failed`` after one real run; if they don't, the
worker has its own bug — separate issue).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.verifier.enqueue import maybe_enqueue_for_deliverable
from backend.src.models import Deliverable, ProofState
from backend.src.queue.streams import RedisStreamManager

logger = structlog.get_logger(__name__)


_DEFAULT_STALE_AFTER_S = 60
_DEFAULT_MAX_RETRIES = 3
_PER_CYCLE_CAP = 50


async def reclaim_stale_verifications(
    session: AsyncSession,
    stream_manager: RedisStreamManager,
    *,
    stale_after_s: int = _DEFAULT_STALE_AFTER_S,
    max_retries: int = _DEFAULT_MAX_RETRIES,
) -> int:
    """Re-enqueue verification for stale ``verification_missing``
    deliverables. Returns the number re-enqueued.

    Selection:
    - ``proof_state == verification_missing``
    - ``verifier_type IS NOT NULL`` (LLM did emit a block)
    - ``created_at < now() - stale_after_s`` (past the normal window)
    - ``verifier_inputs.retry_count < max_retries``

    Per-cycle row cap (``_PER_CYCLE_CAP``) keeps a single tick from
    flooding the queue if a backlog accumulates.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_after_s)
    stmt = (
        select(Deliverable)
        .where(
            Deliverable.proof_state == ProofState.verification_missing,
            Deliverable.verifier_type.is_not(None),
            Deliverable.created_at < cutoff,
        )
        .order_by(Deliverable.created_at.asc())
        .limit(_PER_CYCLE_CAP)
    )
    rows = (await session.execute(stmt)).scalars().all()

    reenqueued = 0
    for d in rows:
        inputs = dict(d.verifier_inputs or {})
        retry_count = int(inputs.get("retry_count", 0))
        if retry_count >= max_retries:
            continue
        inputs["retry_count"] = retry_count + 1
        d.verifier_inputs = inputs
        try:
            await maybe_enqueue_for_deliverable(stream_manager, d)
        except Exception:  # noqa: BLE001 — never break the reclaim cycle
            logger.warning(
                "stale_verification_reenqueue_failed",
                deliverable_id=str(d.id),
                exc_info=True,
            )
            continue
        reenqueued += 1
        # SQLite (test) returns naive datetimes; PG returns aware.
        # Normalise to aware-UTC before subtraction so the test
        # harness and prod path behave identically.
        created_at = d.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        logger.info(
            "stale_verification_reenqueued",
            deliverable_id=str(d.id),
            attempt=inputs["retry_count"],
            stale_for_s=int((datetime.now(timezone.utc) - created_at).total_seconds()),
        )
    return reenqueued
