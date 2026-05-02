"""SSE endpoint for per-project event stream.

The frontend opens an EventSource against ``/api/v1/projects/{id}/events?token=...``
and receives ``message`` / ``run_transition`` / ``deliverable`` /
``decision`` events as they happen. Replaces the prior 3-second
polling loop on chat / deliverables / decisions.

Auth: EventSource cannot send custom headers, so we accept the JWT via
the ``token`` query parameter — the existing ``get_current_user``
dependency already supports this fallback.

S3-2: ``run_transition`` events also flow through a
``ResilientRunEventSource`` that prefers Redis Streams (cross-instance
fan-out) and falls back to PG polling against ``ExecutionRunHistory``
when Redis is unavailable. The in-process ``ProjectEventBus`` is kept
as the primary path for ``message`` / ``deliverable`` / ``decision``
events (single-uvicorn deployments) and as a low-latency same-process
shortcut for run transitions.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from backend.src.core.auth import get_current_user
from backend.src.core.project_events import get_project_event_bus
from backend.src.core.run_event_source import ResilientRunEventSource
from backend.src.core.tenant_context import get_tenant_id
from backend.src.models import Project
from backend.src.storage.database import async_session, get_db

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/projects", tags=["project-events"])


_HEARTBEAT_SECONDS = 15
_OPEN_RETRY_MS = 3000  # browser reconnects after 3s if disconnected


async def _ensure_owns_project(db: AsyncSession, project_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
    stmt = select(Project.id).where(Project.id == project_id, Project.tenant_id == tenant_id)
    if (await db.execute(stmt)).scalar_one_or_none() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")


def _format_sse(event_type: str, payload: dict | str) -> str:
    """Wrap a payload in the SSE wire format."""
    if isinstance(payload, dict):
        body = json.dumps(payload, ensure_ascii=False)
    else:
        body = payload
    return f"event: {event_type}\ndata: {body}\n\n"


async def _stream(project_id: uuid.UUID, *, resilient_source: ResilientRunEventSource | None) -> AsyncIterator[str]:
    bus = get_project_event_bus()
    yield f"retry: {_OPEN_RETRY_MS}\n\n"
    yield _format_sse("ready", {"project_id": str(project_id)})

    sub_iter = bus.subscribe(project_id).__aiter__()

    # Multi-source fan-in: the in-process bus stays primary for
    # message/deliverable/decision events; the resilient source adds
    # cross-instance run_transition events with PG polling fallback
    # when Redis is unavailable.
    bus_task: asyncio.Task[dict] | None = None
    redis_task: asyncio.Task[dict] | None = None
    redis_iter = resilient_source.iter_events().__aiter__() if resilient_source is not None else None

    last_event_at = asyncio.get_event_loop().time()
    try:
        while True:
            timeout = _HEARTBEAT_SECONDS - (asyncio.get_event_loop().time() - last_event_at)
            if timeout <= 0:
                yield _format_sse("heartbeat", {"t": "ping"})
                last_event_at = asyncio.get_event_loop().time()
                continue

            if bus_task is None:
                bus_task = asyncio.create_task(sub_iter.__anext__())
            if redis_iter is not None and redis_task is None:
                redis_task = asyncio.create_task(redis_iter.__anext__())

            pending = {t for t in (bus_task, redis_task) if t is not None}
            done, _ = await asyncio.wait(pending, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            if not done:
                yield _format_sse("heartbeat", {"t": "ping"})
                last_event_at = asyncio.get_event_loop().time()
                continue

            for task in done:
                try:
                    event = task.result()
                except StopAsyncIteration:
                    if task is bus_task:
                        bus_task = None
                    elif task is redis_task:
                        redis_task = None
                    continue
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 — never break the SSE response on a single source error
                    logger.warning(
                        "sse_source_error",
                        project_id=str(project_id),
                        source="bus" if task is bus_task else "redis",
                        exc_info=True,
                    )
                    if task is bus_task:
                        bus_task = None
                    elif task is redis_task:
                        redis_task = None
                    continue
                last_event_at = asyncio.get_event_loop().time()
                if task is bus_task:
                    bus_task = None
                    event_type = str(event.get("type") or "message")
                    yield _format_sse(event_type, event)
                elif task is redis_task:
                    redis_task = None
                    event_type = str(event.get("event") or "run_transition")
                    yield _format_sse(event_type, event.get("data") or {})
    finally:
        for task in (bus_task, redis_task):
            if task is not None and not task.done():
                task.cancel()
        if resilient_source is not None:
            resilient_source.stop()


@router.get("/{project_id}/events")
async def project_events(
    project_id: uuid.UUID,
    request: Request,
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    await _ensure_owns_project(db, project_id, tenant_id)

    resilient_source = _build_resilient_source(request, project_id)

    return StreamingResponse(
        _stream(project_id, resilient_source=resilient_source),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering if proxied
        },
    )


def _build_resilient_source(request: Request, project_id: uuid.UUID) -> ResilientRunEventSource | None:
    """Construct the Redis-or-PG run-event source if a stream manager is
    available. In tests / single-uvicorn dev with no Redis attached, we
    skip the resilient source entirely and the in-process bus carries
    every event."""
    stream_manager = getattr(request.app.state, "stream_manager", None)
    if stream_manager is None:
        return None
    return ResilientRunEventSource(
        project_id=project_id,
        stream_manager=stream_manager,
        session_maker=async_session,
    )
