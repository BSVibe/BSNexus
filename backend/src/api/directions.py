from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.auth import get_current_user
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

router = APIRouter(prefix="/api/v1/directions", tags=["directions"])


@router.post("", response_model=DirectionAckResponse, status_code=status.HTTP_201_CREATED)
async def post_direction(
    payload: DirectionCreate,
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

    direction = DirectionResponse.model_validate(result.direction)
    request = RequestResponse.model_validate(result.request) if result.request is not None else None

    routing = None
    acknowledgement = "Direction accepted. A request has been opened."
    if result.routing_required:
        acknowledgement = "Direction saved. Choose a project before BSNexus opens a request."
        routing = DirectionRoutingPrompt(
            question="Which project should this direction apply to?",
            options=[
                DirectionRoutingOption(project_id=project.id, name=project.name)
                for project in result.routing_options
            ],
        )

    return DirectionAckResponse(
        direction=direction,
        request=request,
        routing=routing,
        acknowledgement=acknowledgement,
    )
