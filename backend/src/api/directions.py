from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request as HttpRequest, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.auth import get_current_user, require_permission
from backend.src.core.directions import ingest_direction
from backend.src.core.tenant_context import get_tenant_id
from backend.src.schemas import (
    DirectionAckResponse,
    DirectionCreate,
    DirectionResponse,
    DirectionRoutingOption,
    DirectionRoutingPrompt,
    RequestResponse,
)
from backend.src.storage.database import get_db
from backend.src.workers.request_worker import REQUEST_QUEUE_STREAM

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/directions", tags=["directions"])


@router.post(
    "",
    response_model=DirectionAckResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("bsnexus.directions.write"))],
)
async def post_direction(
    payload: DirectionCreate,
    http_request: HttpRequest,
    user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> DirectionAckResponse:
    actor_id = str(getattr(user, "id", "unknown"))
    try:
        result = await ingest_direction(
            payload=payload,
            tenant_id=tenant_id,
            actor_id=actor_id,
            session=db,
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found") from exc

    # G9 — when the Direction resolved to a concrete Request (project
    # routing succeeded), hand it to the RequestWorker. The HTTP
    # response returns immediately; the multi-round LLM tool loop runs
    # on the worker. A routing-required Direction has no Request yet —
    # nothing to enqueue.
    if result.request is not None:
        stream_manager = getattr(http_request.app.state, "stream_manager", None)
        if stream_manager is not None:
            await stream_manager.publish(
                REQUEST_QUEUE_STREAM,
                {
                    "request_id": str(result.request.id),
                    "tenant_id": str(tenant_id),
                },
            )
        else:
            # No stream manager (test app without Redis) — the Request
            # row still exists; an operator/test can drive it directly.
            logger.warning(
                "direction_request_not_enqueued_no_stream_manager",
                request_id=str(result.request.id),
            )

    direction = DirectionResponse.model_validate(result.direction)
    request = RequestResponse.model_validate(result.request) if result.request is not None else None

    routing = None
    if result.routing_required:
        routing = DirectionRoutingPrompt(
            question="Which project should this direction apply to?",
            options=[
                DirectionRoutingOption(project_id=project.id, name=project.name)
                for project in result.routing_options
            ],
        )

    # Wire shape carries state only (request != None vs routing != None);
    # display copy is rendered by the client in the active locale.
    return DirectionAckResponse(direction=direction, request=request, routing=routing)
