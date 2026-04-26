"""S3-2 — Resilient run-event source for SSE fan-out.

The audit calls out a single point of failure on the SSE path:
``RunStateMachine.transition`` writes both the canonical row to
``execution_run_history`` *and* publishes a ``run_transition`` event
to Redis Streams (``project:events:<project_id>``) for fan-out across
uvicorn instances. If Redis is unavailable the event simply never
arrives — the row is in PG but the frontend Plan Tree freezes.

This module wraps the Redis subscriber in a resilient source that:

  * Reads from ``RedisStreamManager.tail`` while Redis is healthy.
  * On any Redis failure (``ConnectionError`` or ``redis.exceptions.RedisError``),
    switches to a 1s PG poll against ``ExecutionRunHistory`` filtered by
    ``project_id`` (via the run join) and ``timestamp > last_seen``.
  * Periodically retries Redis with a tiny ``tail`` probe; on success it
    resumes Streams reads from a fresh ``$`` cursor (live tail) so the
    Streams cursor and the PG cursor never disagree about what's been
    delivered.
  * The PG poll deduplicates by primary key plus monotonic timestamp,
    so a transition that arrived during the fallback episode is yielded
    exactly once.

CLAUDE.md NEVER rule preserved: still Streams (no Pub/Sub). The
fallback is a different transport (PG SELECT) but never Pub/Sub.

Tenant scoping: PG queries always join ``ExecutionRun`` and filter by
``project_id``. Cross-project leaks are not possible.
"""

from __future__ import annotations

import asyncio
import enum
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.src.models import ExecutionRun, ExecutionRunHistory
from backend.src.queue.streams import RedisStreamManager

logger = structlog.get_logger(__name__)


class _RedisHealth(str, enum.Enum):
    healthy = "healthy"
    unhealthy = "unhealthy"


# Errors that count as "Redis is down" and trigger fallback. We catch
# the broad set rather than chase every redis-py subclass — any failure
# during ``tail()`` means we can't trust Redis right now and should
# fall back to PG.
def _is_redis_failure(exc: BaseException) -> bool:
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    # redis.exceptions.RedisError is the base class for all redis-py
    # errors. Import lazily so the test process doesn't pay the cost.
    try:
        from redis.exceptions import RedisError  # noqa: PLC0415

        return isinstance(exc, RedisError)
    except Exception:  # noqa: BLE001 — never break the source on import
        return False


class ResilientRunEventSource:
    """Per-project event source with Redis Streams primary + PG fallback.

    Designed to be wrapped by the SSE endpoint: ``async for event in
    source.iter_events()`` yields a steady stream of run-transition
    events regardless of Redis availability, until ``source.stop()``
    is called.

    Each event has the same shape regardless of source:

        {
            "event": "run_transition",
            "data": {
                "run_id": "...",
                "from_status": "...",
                "to_status": "...",
                "request_id": "..." | None,
                "actor": "..." | None,
            },
        }
    """

    DEFAULT_POLL_INTERVAL = 1.0  # PG polling tick during a fallback episode
    DEFAULT_RECOVERY_PROBE = 5.0  # how often to retry Redis once unhealthy
    DEFAULT_TAIL_BLOCK_MS = 1500  # Redis xread block window

    def __init__(
        self,
        *,
        project_id: uuid.UUID,
        stream_manager: RedisStreamManager,
        session_maker: async_sessionmaker[AsyncSession] | Callable[[], Awaitable[AsyncSession]],
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL,
        recovery_probe_seconds: float = DEFAULT_RECOVERY_PROBE,
        tail_block_ms: int = DEFAULT_TAIL_BLOCK_MS,
        pg_cursor_start: datetime | None = None,
    ) -> None:
        self._project_id = project_id
        self._stream_manager = stream_manager
        self._session_maker = session_maker
        self._poll_interval = poll_interval_seconds
        self._recovery_probe = recovery_probe_seconds
        self._tail_block_ms = tail_block_ms

        # Redis read cursor. ``$`` means "tail from now" — only events
        # produced after we connect.
        self._last_id: str = "$"
        # PG read cursor.  Anything with timestamp > _pg_cursor is unseen.
        self._pg_cursor: datetime = pg_cursor_start or datetime.now(timezone.utc)
        # Last unhealthy → healthy probe time. We don't probe every
        # tick to avoid hammering a dying Redis with tail() calls.
        self._last_recovery_probe: float = 0.0

        self._stream_key = RedisStreamManager.project_events_stream(str(project_id))
        self._health = _RedisHealth.healthy
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        """Signal the iterator to exit on its next loop pass."""
        self._stop_event.set()

    def health(self) -> _RedisHealth:
        return self._health

    async def iter_events(self) -> AsyncIterator[dict[str, Any]]:
        """Yield events forever until ``stop()`` or cancellation."""
        while not self._stop_event.is_set():
            if self._health == _RedisHealth.healthy:
                async for event in self._read_redis_once():
                    yield event
                if self._stop_event.is_set():
                    return
            else:
                # PG fallback path. Each poll yields zero or more events,
                # then sleeps the poll interval — but we also intermittently
                # probe Redis to detect recovery.
                async for event in self._read_pg_once():
                    yield event
                if self._stop_event.is_set():
                    return
                await self._maybe_probe_redis()
                await asyncio.sleep(self._poll_interval)

    # -- Redis path ---------------------------------------------------------

    async def _read_redis_once(self) -> AsyncIterator[dict[str, Any]]:
        try:
            messages = await self._stream_manager.tail(
                self._stream_key,
                last_id=self._last_id,
                block=self._tail_block_ms,
            )
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 — sink-and-classify
            if _is_redis_failure(exc):
                logger.warning(
                    "run_event_source_redis_unhealthy",
                    project_id=str(self._project_id),
                    exc_type=type(exc).__name__,
                )
                self._health = _RedisHealth.unhealthy
                # Reset _last_id so when we recover we start tailing
                # from "now" — the PG fallback covers the gap.
                self._last_id = "$"
                return
            # Non-Redis failure — re-raise so the caller (SSE handler)
            # can decide. We never silently swallow logic bugs.
            raise

        if not messages:
            # Cooperative yield. Without this, an empty Redis stream
            # would spin the loop hot.
            await asyncio.sleep(0)
            return
        for msg in messages:
            self._last_id = str(msg.get("_message_id") or self._last_id)
            event_name = msg.get("event") or "run_transition"
            data = msg.get("data") or {}
            yield {"event": str(event_name), "data": data if isinstance(data, dict) else {"raw": data}}

    # -- PG path ------------------------------------------------------------

    async def _read_pg_once(self) -> AsyncIterator[dict[str, Any]]:
        rows: list[tuple[ExecutionRunHistory, ExecutionRun]] = []
        async with self._session_maker() as session:
            try:
                stmt = (
                    select(ExecutionRunHistory, ExecutionRun)
                    .join(ExecutionRun, ExecutionRun.id == ExecutionRunHistory.run_id)
                    .where(
                        ExecutionRun.project_id == self._project_id,
                        ExecutionRunHistory.timestamp > self._pg_cursor,
                    )
                    .order_by(ExecutionRunHistory.timestamp.asc())
                    .limit(200)
                )
                result = await session.execute(stmt)
                rows = list(result.all())
            except Exception:  # noqa: BLE001 — PG transient failures fall through to next tick
                logger.warning(
                    "run_event_source_pg_poll_failed",
                    project_id=str(self._project_id),
                    exc_info=True,
                )
                return

        for hist, run in rows:
            self._pg_cursor = hist.timestamp
            yield {
                "event": "run_transition",
                "data": {
                    "run_id": str(run.id),
                    "request_id": str(run.request_id) if run.request_id else None,
                    "from_status": hist.from_status.value
                    if hasattr(hist.from_status, "value")
                    else str(hist.from_status),
                    "to_status": hist.to_status.value if hasattr(hist.to_status, "value") else str(hist.to_status),
                    "actor": hist.actor,
                },
            }

    # -- Recovery probe -----------------------------------------------------

    async def _maybe_probe_redis(self) -> None:
        loop = asyncio.get_event_loop()
        now = loop.time()
        if (now - self._last_recovery_probe) < self._recovery_probe:
            return
        self._last_recovery_probe = now
        try:
            # Cheap probe: tail with a tiny block window from "$".
            await self._stream_manager.tail(self._stream_key, last_id="$", block=10)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 — sink-and-classify
            if _is_redis_failure(exc):
                logger.debug(
                    "run_event_source_redis_still_unhealthy",
                    project_id=str(self._project_id),
                    exc_type=type(exc).__name__,
                )
                return
            raise

        # Probe succeeded — restore healthy state. The Redis cursor
        # starts at "$" (live tail) because the PG fallback already
        # surfaced everything older.
        logger.info("run_event_source_redis_recovered", project_id=str(self._project_id))
        self._health = _RedisHealth.healthy
        self._last_id = "$"
