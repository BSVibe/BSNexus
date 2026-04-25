"""SSE endpoint for per-project event stream.

The frontend opens an EventSource against ``/api/v1/projects/{id}/events?token=...``
and receives ``message`` / ``run_transition`` / ``deliverable`` /
``decision`` events as they happen. Replaces the prior 3-second
polling loop on chat / deliverables / decisions.

Auth: EventSource cannot send custom headers, so we accept the JWT via
the ``token`` query parameter — the existing ``get_current_user``
dependency already supports this fallback.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from backend.src.core.auth import get_current_user
from backend.src.core.project_events import get_project_event_bus
from backend.src.core.tenant_context import get_tenant_id
from backend.src.models import Project
from backend.src.storage.database import get_db

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


async def _stream(project_id: uuid.UUID) -> AsyncIterator[str]:
    bus = get_project_event_bus()
    yield f"retry: {_OPEN_RETRY_MS}\n\n"
    yield _format_sse("ready", {"project_id": str(project_id)})

    sub = bus.subscribe(project_id)
    sub_iter = sub.__aiter__()
    last_event_at = asyncio.get_event_loop().time()
    while True:
        timeout = _HEARTBEAT_SECONDS - (asyncio.get_event_loop().time() - last_event_at)
        if timeout <= 0:
            yield _format_sse("heartbeat", {"t": "ping"})
            last_event_at = asyncio.get_event_loop().time()
            continue
        try:
            event = await asyncio.wait_for(sub_iter.__anext__(), timeout=timeout)
        except asyncio.TimeoutError:
            yield _format_sse("heartbeat", {"t": "ping"})
            last_event_at = asyncio.get_event_loop().time()
            continue
        except StopAsyncIteration:
            break
        last_event_at = asyncio.get_event_loop().time()
        event_type = str(event.get("type") or "message")
        yield _format_sse(event_type, event)


@router.get("/{project_id}/events")
async def project_events(
    project_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
) -> StreamingResponse:
    await _ensure_owns_project(db, project_id, tenant_id)
    return StreamingResponse(
        _stream(project_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering if proxied
        },
    )
