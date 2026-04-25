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

import asyncio
import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.auth import get_current_user
from backend.src.core.dispatcher import _dispatch_background, build_adapter
from backend.src.core.request_extractor import RequestExtractor
from backend.src.core.tenant_context import get_tenant_id
from backend.src.models import (
    ConversationMessage,
    ExecutionRun,
    Project,
    RunPriority,
    RunStatus,
)
from backend.src.schemas import MessageCreate, MessageResponse, SendMessageResponse
from backend.src.storage.database import get_db

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/projects", tags=["conversation"])


def _build_ack_content(intent_summary: str) -> str:
    """Short, language-neutral acknowledgment for the founder chat.

    Shown immediately when a run is dispatched. The final result reply
    (via publish_run_output) replaces this as the "authoritative"
    assistant turn — this message is the equivalent of "Got it, on it".
    """
    snippet = (intent_summary or "").strip()
    if len(snippet) > 160:
        snippet = snippet[:157] + "…"
    if snippet:
        return f"⚡ Starting work on: **{snippet}**"
    return "⚡ On it."


async def _request_has_active_run(db: AsyncSession, request_id: uuid.UUID) -> bool:
    stmt = select(ExecutionRun.id).where(
        ExecutionRun.request_id == request_id,
        ExecutionRun.status.in_((RunStatus.pending, RunStatus.running)),
    )
    return (await db.execute(stmt)).first() is not None


async def _require_project(db: AsyncSession, project_id: uuid.UUID, tenant_id: uuid.UUID) -> Project:
    stmt = select(Project).where(Project.id == project_id, Project.tenant_id == tenant_id)
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
    request: Request,
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
    outcome = await extractor.process_message(message, tenant_id=tenant_id, db=db)

    # Capture the founder's Bearer token so post-run sibling-service
    # calls (BSage index) can forward the same identity. Auto same-
    # account SSO without a separate service key.
    auth_header = request.headers.get("authorization", "") or request.headers.get("Authorization", "")
    originator_token: str | None = None
    if auth_header.lower().startswith("bearer "):
        originator_token = auth_header.split(" ", 1)[1].strip() or None
    if outcome.request is not None and originator_token:
        outcome.request.originator_auth = originator_token

    # Run dispatch policy:
    #
    # - chit_chat / question → no run, no ack. Just the user message.
    # - request (new Request created) → seed one ExecutionRun pending; the
    #   background dispatcher's Phase 0 calls the replanner for the
    #   first iteration. Insert a chip-style ack so the chat doesn't
    #   look frozen during the replanner LLM round-trip.
    # - modification on an already-running Request → DO NOT seed a new
    #   run. The replanner pulls recent founder messages on every
    #   iteration; the modification will land in the next iteration's
    #   plan automatically. Insert a small mod_ack so the founder sees
    #   the steer was received.
    run_to_dispatch: uuid.UUID | None = None
    if outcome.request is not None:
        active_run_exists = await _request_has_active_run(db, outcome.request.id)
        if active_run_exists and not outcome.created_new:
            # Modification riding on an in-flight chain — let it ride.
            mod_ack = ConversationMessage(
                project_id=project_id,
                role="assistant",
                content=("확인했어요. 진행 중인 작업이 끝나면 이 변경사항 반영해서 다음 단계 잡을게요."),
                request_id=outcome.request.id,
                actions=[{"kind": "mod_ack"}],
            )
            db.add(mod_ack)
        else:
            seeded_run = ExecutionRun(
                tenant_id=tenant_id,
                project_id=project_id,
                request_id=outcome.request.id,
                status=RunStatus.pending,
                priority=RunPriority.medium,
            )
            db.add(seeded_run)
            await db.flush()
            run_to_dispatch = seeded_run.id

            ack_msg = ConversationMessage(
                project_id=project_id,
                role="assistant",
                content=_build_ack_content(outcome.request.intent_summary),
                request_id=outcome.request.id,
                actions=[{"kind": "ack", "run_id": str(seeded_run.id)}],
            )
            db.add(ack_msg)

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
        run_dispatched=bool(run_to_dispatch),
    )

    if run_to_dispatch is not None:
        stream_manager = getattr(request.app.state, "stream_manager", None)
        asyncio.create_task(_BACKGROUND_DISPATCH(run_to_dispatch, tenant_id, project_id, stream_manager))

    return SendMessageResponse(
        message=MessageResponse.model_validate(message, from_attributes=True),
        intent=outcome.intent.value,
        request_id=outcome.request.id if outcome.request else None,
        request_created=outcome.created_new,
        intent_summary=(outcome.request.intent_summary if outcome.request else None),
    )


_dispatch_new_run = _dispatch_background
_build_adapter = build_adapter
_BACKGROUND_DISPATCH = _dispatch_new_run
