"""Background supervisor that runs one ChannelFanout per active project.

The supervisor periodically scans the project_channels table, groups
rows by project, and ensures every project with at least one active
channel has a ``ChannelFanout`` task running. New channels are picked
up on the next scan; deleted channels stop their fanout when the
supervisor next polls.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import structlog
from sqlalchemy import select

from backend.src.core.channel_adapter import ChannelFanout, ChannelTarget
from backend.src.models import ProjectChannel
from backend.src.queue.streams import RedisStreamManager
from backend.src.storage.database import async_session

logger = structlog.get_logger(__name__)

SUPERVISOR_INTERVAL_SECONDS = 30.0


class ChannelSupervisor:
    """Single instance, lives on ``app.state.channel_supervisor``."""

    def __init__(self, stream_manager: RedisStreamManager) -> None:
        self._stream = stream_manager
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        # project_id -> (fanout instance, asyncio task running it)
        self._fanouts: dict[uuid.UUID, tuple[ChannelFanout, asyncio.Task[None]]] = {}

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="channel-supervisor")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except asyncio.TimeoutError:
                self._task.cancel()
                try:
                    await self._task
                except (asyncio.CancelledError, BaseException):  # noqa: BLE001
                    pass
        # Stop every active fanout, then await the cancellation so we don't
        # leave background coroutines hanging on the next event loop tick.
        for fanout, task in list(self._fanouts.values()):
            fanout.stop()
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, BaseException):  # noqa: BLE001
                pass
        self._fanouts.clear()
        self._task = None

    async def _run(self) -> None:
        logger.info("channel_supervisor_started", interval=SUPERVISOR_INTERVAL_SECONDS)
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception:  # noqa: BLE001
                logger.exception("channel_supervisor_tick_failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=SUPERVISOR_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                continue
        logger.info("channel_supervisor_stopped")

    async def tick(self) -> None:
        """One reconciliation pass. Public for tests."""
        targets_by_project = await self._load_active_targets()

        # Stop fanouts whose project has no more active channels.
        for project_id in list(self._fanouts.keys()):
            if project_id not in targets_by_project:
                fanout, task = self._fanouts.pop(project_id)
                fanout.stop()
                task.cancel()

        # Start fanouts for new projects.
        for project_id, targets in targets_by_project.items():
            if project_id in self._fanouts:
                continue
            fanout = ChannelFanout(project_id, targets, self._stream)
            task = asyncio.create_task(
                fanout.run(), name=f"channel-fanout-{project_id}"
            )
            self._fanouts[project_id] = (fanout, task)

    async def _load_active_targets(self) -> dict[uuid.UUID, list[ChannelTarget]]:
        async with async_session() as db:
            result = await db.execute(
                select(ProjectChannel).where(ProjectChannel.is_active.is_(True))
            )
            rows = list(result.scalars().all())

        grouped: dict[uuid.UUID, list[ChannelTarget]] = {}
        for row in rows:
            grouped.setdefault(row.project_id, []).append(
                ChannelTarget(
                    kind=row.kind,
                    external_channel_id=row.external_channel_id,
                    credentials=_decode_credentials(row.credentials_encrypted),
                )
            )
        return grouped


def _decode_credentials(blob: str | None) -> dict[str, Any]:
    if not blob:
        return {}
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        return {}


# ── lifespan helpers ─────────────────────────────────────────────


async def start_channel_supervisor(app: Any) -> ChannelSupervisor:
    stream_manager: RedisStreamManager = app.state.stream_manager
    supervisor = ChannelSupervisor(stream_manager)
    supervisor.start()
    app.state.channel_supervisor = supervisor
    return supervisor


async def stop_channel_supervisor(app: Any) -> None:
    supervisor: ChannelSupervisor | None = getattr(app.state, "channel_supervisor", None)
    if supervisor is not None:
        await supervisor.stop()
        app.state.channel_supervisor = None
