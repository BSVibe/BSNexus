"""In-process pub/sub for per-project SSE streams.

The founder's chat needs near-real-time push: when the replanner picks
a next_step, when a phase_done lands, when a Decision opens. Polling
every 3s is the legacy compromise — this module is the replacement.

Single-server scope: BSNexus today runs as one uvicorn process. A
process-local ``asyncio.Queue`` per project subscriber is enough.
``RedisStreamManager`` does Streams (consumer groups, exclusive
delivery) which is the wrong shape for fan-out; CLAUDE.md prohibits
Redis pub/sub. When BSNexus eventually scales out we'll swap this for
postgres LISTEN/NOTIFY or fan-out via a stream-per-project + an
SSE-fanout layer. The Protocol below keeps that swap local.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class ProjectEventBus:
    """Per-project broadcast over asyncio.Queue.

    Each subscriber gets its own queue; ``publish`` fans out to all
    queues for the project. Slow consumers don't block publishers — if
    a queue is full (1000 events deep), older events for that consumer
    drop with a warning.
    """

    _MAX_QUEUE = 1000

    def __init__(self) -> None:
        self._subs: dict[uuid.UUID, set[asyncio.Queue[dict[str, Any]]]] = {}

    async def publish(self, project_id: uuid.UUID, event: dict[str, Any]) -> None:
        queues = list(self._subs.get(project_id, ()))
        for q in queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("project_event_dropped", project_id=str(project_id))

    async def subscribe(self, project_id: uuid.UUID) -> AsyncIterator[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._MAX_QUEUE)
        self._subs.setdefault(project_id, set()).add(q)
        try:
            while True:
                event = await q.get()
                yield event
        finally:
            self._subs[project_id].discard(q)
            if not self._subs[project_id]:
                self._subs.pop(project_id, None)


_singleton: ProjectEventBus | None = None


def get_project_event_bus() -> ProjectEventBus:
    global _singleton
    if _singleton is None:
        _singleton = ProjectEventBus()
    return _singleton


async def publish_message(
    project_id: uuid.UUID,
    *,
    message_id: uuid.UUID,
    role: str,
    content: str,
    request_id: uuid.UUID | None,
    actions: list[dict[str, Any]] | None,
    created_at: str,
) -> None:
    """Convenience: publish a chat message event."""
    await get_project_event_bus().publish(
        project_id,
        {
            "type": "message",
            "id": str(message_id),
            "role": role,
            "content": content,
            "request_id": str(request_id) if request_id else None,
            "actions": list(actions or []),
            "created_at": created_at,
        },
    )


async def publish_run_transition(
    project_id: uuid.UUID,
    *,
    run_id: uuid.UUID,
    request_id: uuid.UUID | None,
    from_status: str,
    to_status: str,
) -> None:
    await get_project_event_bus().publish(
        project_id,
        {
            "type": "run_transition",
            "run_id": str(run_id),
            "request_id": str(request_id) if request_id else None,
            "from": from_status,
            "to": to_status,
        },
    )


async def publish_deliverable(
    project_id: uuid.UUID,
    *,
    deliverable_id: uuid.UUID,
    title: str,
    type_: str,
    status: str,
) -> None:
    await get_project_event_bus().publish(
        project_id,
        {
            "type": "deliverable",
            "id": str(deliverable_id),
            "title": title,
            "deliverable_type": type_,
            "status": status,
        },
    )


async def publish_decision(
    project_id: uuid.UUID,
    *,
    decision_id: uuid.UUID,
    request_id: uuid.UUID | None,
    question: str,
    blocking: bool,
) -> None:
    await get_project_event_bus().publish(
        project_id,
        {
            "type": "decision",
            "id": str(decision_id),
            "request_id": str(request_id) if request_id else None,
            "question": question,
            "blocking": blocking,
        },
    )


async def publish_run_output_chunk(
    project_id: uuid.UUID,
    *,
    run_id: uuid.UUID,
    chunk: str,
    finish_reason: str | None = None,
) -> None:
    """Direction reset 2026-05-03 — Inside panel live output stream.

    BSGatewayAdapter feeds each ``delta.content`` chunk through here so
    the founder watches claude type in real time. ``finish_reason`` is
    set on the terminal chunk only.

    Distinct from :func:`backend.src.core.run_artifacts.publish_run_output`,
    which materialises the *terminal* deliverable + chat reply once a
    Run finishes — they're orthogonal stages of the same Run lifecycle.
    """
    await get_project_event_bus().publish(
        project_id,
        {
            "type": "run_output",
            "run_id": str(run_id),
            "content": chunk,
            "finish_reason": finish_reason,
        },
    )


async def publish_decision_resolved(
    project_id: uuid.UUID,
    *,
    decision_id: uuid.UUID,
    resolution: str,
    resolved_by: str | None,
) -> None:
    """Fired from the resolve API after commit so the Decisions tab
    can dismiss the resolved row immediately and the Inside panel
    can flip its blocked-on-decision banner."""
    await get_project_event_bus().publish(
        project_id,
        {
            "type": "decision_resolved",
            "id": str(decision_id),
            "resolution": resolution,
            "resolved_by": resolved_by,
        },
    )
