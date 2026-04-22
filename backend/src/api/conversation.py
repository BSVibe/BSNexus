"""Conversation API — list + send project chat messages.

Each user message is run through ``RequestExtractor``:

- ``chit_chat`` / ``question`` → message persists, no Request side effect.
- ``request``                  → new Request row created.
- ``modification``             → appended to the latest open Request, or
                                 a new Request if none exists.

Response exposes the classification so the frontend can render a
"요청이 열렸어요" chip.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.auth import get_current_user
from backend.src.core.request_extractor import MessageIntent, RequestExtractor
from backend.src.core.tenant_context import get_tenant_id
from backend.src.models import ConversationMessage, Project
from backend.src.schemas import MessageCreate, MessageResponse, SendMessageResponse
from backend.src.storage.database import get_db

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/projects", tags=["conversation"])


async def _require_project(
    db: AsyncSession, project_id: uuid.UUID, tenant_id: uuid.UUID
) -> Project:
    stmt = select(Project).where(
        Project.id == project_id, Project.tenant_id == tenant_id
    )
    project = (await db.execute(stmt)).scalar_one_or_none()
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


@router.get(
    "/{project_id}/messages",
    response_model=list[MessageResponse],
)
async def list_messages(
    project_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
) -> list[ConversationMessage]:
    await _require_project(db, project_id, tenant_id)
    stmt = (
        select(ConversationMessage)
        .where(ConversationMessage.project_id == project_id)
        .order_by(ConversationMessage.created_at.asc())
    )
    return list((await db.execute(stmt)).scalars())


@router.post(
    "/{project_id}/messages",
    response_model=SendMessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def send_message(
    project_id: uuid.UUID,
    payload: MessageCreate,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
) -> SendMessageResponse:
    await _require_project(db, project_id, tenant_id)

    message = ConversationMessage(
        project_id=project_id,
        role="user",
        content=payload.content,
    )
    db.add(message)
    await db.flush()

    extractor = RequestExtractor()  # static classifier by default
    outcome = await extractor.process_message(
        message, tenant_id=tenant_id, db=db
    )

    await db.commit()
    await db.refresh(message)
    if outcome.request is not None:
        await db.refresh(outcome.request)

    logger.info(
        "message_sent",
        project_id=str(project_id),
        message_id=str(message.id),
        intent=outcome.intent.value,
        request_created=outcome.created_new,
    )

    return SendMessageResponse(
        message=MessageResponse.model_validate(message, from_attributes=True),
        intent=outcome.intent.value,
        request_id=outcome.request.id if outcome.request else None,
        request_created=outcome.created_new,
        intent_summary=(
            outcome.request.intent_summary if outcome.request else None
        ),
    )
