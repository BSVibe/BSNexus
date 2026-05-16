"""Greenfield project SSE endpoint (G7.2).

``GET /api/v1/events?project_id=…`` opens a tenant-scoped Server-Sent
Events stream that fans Redis Streams (``project:events:{project_id}``)
onto the wire. Frontend consumer: ``frontend/src/hooks/useProjectEvents``
(``EventSource`` with auto-reconnect).

Why SSE not WebSocket:
- One-way server → client suffices for this surface (no client → server
  RPC over the same channel).
- ``EventSource`` ships with browser auto-reconnect honoring the
  ``retry:`` directive.
- Cheap to scale behind the existing FastAPI / Uvicorn workers.

Auth: EventSource cannot send custom headers, so the JWT can also
arrive in ``?token=…``. ``get_current_user`` already handles either
form.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.auth import get_current_user, require_permission
from backend.src.core.tenant_context import get_tenant_id
from backend.src.models import Project
from backend.src.queue.streams import RedisStreamManager
from backend.src.storage.database import get_db

router = APIRouter(prefix="/api/v1/events", tags=["events"])


# Reconnection delay the browser uses on transport drops, in ms. The
# frontend ``useProjectEvents`` reads this via ``EventSource`` auto-
# reconnect; surfacing it as a constant here keeps the contract honest.
_RETRY_MS = 5000

# Heartbeat interval — every N seconds without a real event we send a
# ``heartbeat`` block so proxies / browsers don't think the connection
# stalled. ``RedisStreamManager.tail`` uses XREAD with a block timeout
# in ms; setting it just under the heartbeat cadence lets us emit
# heartbeats deterministically.
_HEARTBEAT_INTERVAL_S = 15
_TAIL_BLOCK_MS = _HEARTBEAT_INTERVAL_S * 1000


def _format_sse(event: str, data: dict | None = None) -> str:
    """Format one SSE block — ``event:`` line + ``data:`` line + blank."""
    payload = json.dumps(data if data is not None else {})
    return f"event: {event}\ndata: {payload}\n\n"


async def _assert_project_belongs(
    db: AsyncSession, project_id: uuid.UUID, tenant_id: uuid.UUID
) -> None:
    stmt = select(Project.id).where(Project.id == project_id, Project.tenant_id == tenant_id)
    if (await db.execute(stmt)).scalar_one_or_none() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")


async def _event_generator(
    request: Request,
    stream_key: str,
    stream_manager: RedisStreamManager,
    project_id: uuid.UUID,
) -> AsyncGenerator[str, None]:
    """Drive the SSE wire format off a Redis Streams tail.

    1. Emit the reconnection-delay directive — browsers honor this on
       transport drops.
    2. Emit ``ready`` so the client can flip its connection-status
       indicator before any domain event lands.
    3. Tail the per-project stream. When ``tail`` returns nothing
       (timeout) emit ``heartbeat`` so the channel stays warm. When it
       returns messages, format each as ``event: <name>\\ndata: <json>``
       and advance ``last_id`` to the message id.
    4. Bail when the client disconnects.
    """
    yield f"retry: {_RETRY_MS}\n\n"
    yield _format_sse("ready", {"project_id": str(project_id)})

    last_id = "$"
    while True:
        if await request.is_disconnected():
            return
        try:
            messages = await stream_manager.tail(
                stream_key, last_id=last_id, block=_TAIL_BLOCK_MS
            )
        except asyncio.CancelledError:
            return
        except Exception:
            # Transport hiccup — emit heartbeat and retry. EventSource
            # will pick up where we left off via ``Last-Event-ID``;
            # we don't resume from a stored cursor across restarts.
            yield _format_sse("heartbeat")
            continue

        if not messages:
            yield _format_sse("heartbeat")
            continue

        for msg in messages:
            last_id = msg.get("_message_id", last_id)
            event_name = str(msg.get("event") or "message")
            data: Any = msg.get("data", {})
            if not isinstance(data, dict):
                # Shouldn't happen — `publish_project_event` always
                # wraps a dict — but guard defensively.
                data = {"value": data}
            yield _format_sse(event_name, data)


@router.get("", dependencies=[Depends(require_permission("bsnexus.events.read"))])
async def stream_project_events(
    request: Request,
    project_id: uuid.UUID = Query(...),
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    await _assert_project_belongs(db, project_id, tenant_id)
    stream_manager: RedisStreamManager = request.app.state.stream_manager
    stream_key = RedisStreamManager.project_events_stream(str(project_id))

    return StreamingResponse(
        _event_generator(request, stream_key, stream_manager, project_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            # Disable nginx-style proxy buffering so each chunk reaches
            # the browser as soon as it's yielded.
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
