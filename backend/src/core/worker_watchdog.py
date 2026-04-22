"""WorkerWatchdog — reconciles orphaned runs when remote workers go offline.

Opt-in (``REMOTE_WORKERS_ENABLED=true``). Polls every 60s. If a run
has ``worker_id`` set but the worker hasn't heartbeated within the
staleness window, return the run to ``pending`` so a healthy worker
can pick it up.

This is the ONLY polling component in the new design. No dependency
promotion (event-driven via RunOrchestrator.on_run_completed). No phase
advancement (phases are gone).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.config import settings
from backend.src.core.state_machine import RunStateMachine
from backend.src.models import ExecutionRun, RunStatus, Worker
from backend.src.queue.streams import RedisStreamManager
from backend.src.storage.database import async_session

logger = structlog.get_logger(__name__)


class WorkerWatchdog:
    """Background task that reclaims orphaned runs. Singleton per app."""

    def __init__(
        self,
        *,
        poll_interval_s: int = 60,
        worker_stale_after_s: int = 120,
        state_machine: RunStateMachine | None = None,
    ):
        self._poll_interval = poll_interval_s
        self._stale_after = worker_stale_after_s
        self._state = state_machine or RunStateMachine()
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None

    async def start(self, stream_manager: RedisStreamManager | None = None) -> None:
        if self._task is not None:
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run(stream_manager))
        logger.info("worker_watchdog_started", interval_s=self._poll_interval)

    async def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        self._task = None
        self._stop_event = None
        logger.info("worker_watchdog_stopped")

    async def _run(self, stream_manager: RedisStreamManager | None) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                async with async_session() as db:
                    reclaimed = await self.reconcile_once(db, stream_manager)
                    if reclaimed:
                        await db.commit()
            except Exception:  # noqa: BLE001
                logger.exception("worker_watchdog_error")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._poll_interval
                )
                return
            except asyncio.TimeoutError:
                continue

    async def reconcile_once(
        self, db: AsyncSession, stream_manager: RedisStreamManager | None
    ) -> int:
        """Return to pending any run whose worker is stale. Returns count."""
        now = datetime.now(timezone.utc)
        staleness_cutoff = now - timedelta(seconds=self._stale_after)

        stmt = (
            select(ExecutionRun, Worker)
            .join(Worker, Worker.id == ExecutionRun.worker_id)
            .where(
                and_(
                    ExecutionRun.status == RunStatus.running,
                    ExecutionRun.worker_id.is_not(None),
                    Worker.last_heartbeat < staleness_cutoff,
                )
            )
        )
        result = await db.execute(stmt)
        rows = list(result.all())
        if not rows:
            return 0

        for run, worker in rows:
            logger.warning(
                "worker_watchdog_reclaiming_run",
                run_id=str(run.id),
                worker_id=str(worker.id),
                last_heartbeat=worker.last_heartbeat.isoformat() if worker.last_heartbeat else None,
            )
            run.worker_id = None
            await self._state.transition(
                run,
                RunStatus.pending,
                reason=f"worker {worker.id} went stale",
                actor="worker_watchdog",
                db_session=db,
                stream_manager=stream_manager,
            )

        return len(rows)


_singleton: WorkerWatchdog | None = None


def get_worker_watchdog() -> WorkerWatchdog:
    global _singleton
    if _singleton is None:
        _singleton = WorkerWatchdog()
    return _singleton


def remote_workers_enabled() -> bool:
    return bool(getattr(settings, "remote_workers_enabled", False))
