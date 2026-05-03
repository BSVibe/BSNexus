"""Conversation API — list + send project chat messages.

Direction reset 2026-05-03 — request extraction is now a single rule:

- non-empty user content ⇒ new Request row, dispatch one ExecutionRun.
- empty user content     ⇒ message persists, no Request side effect.

The previous LLM-driven chit_chat / question / request / modification
classifier is retired with ``request_extractor.py``. Modifications-on-
in-flight-Request UX is folded into the next Request (BSGateway's CLI
agent reads chat history on each turn).
"""

from __future__ import annotations

import asyncio
import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.audit import (
    actor_from_user,
    actor_system,
    resource_request,
    safe_emit,
)
from backend.src.core.auth import get_current_user
from backend.src.core.dispatcher import _dispatch_background, build_adapter
from backend.src.core.tenant_context import get_tenant_id
from backend.src.models import (
    ConversationMessage,
    ExecutionRun,
    Project,
    Request as RequestModel,
    RequestStatus,
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
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
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
    user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> SendMessageResponse:
    await _require_project(db, project_id, tenant_id)

    message = ConversationMessage(
        project_id=project_id,
        role="user",
        content=payload.content,
    )
    db.add(message)
    await db.flush()

    # Direction reset 2026-05-03 — inline rule replaces RequestExtractor.
    # Non-empty user content opens a new Request and seeds one Run.
    request_obj: RequestModel | None = None
    created_new = False
    content_stripped = (payload.content or "").strip()
    if content_stripped:
        request_obj = RequestModel(
            tenant_id=tenant_id,
            project_id=project_id,
            origin_message_id=message.id,
            intent_summary=content_stripped[:240],
            status=RequestStatus.open,
        )
        db.add(request_obj)
        await db.flush()
        message.request_id = request_obj.id
        created_new = True

        # Phase Audit Batch 2 — emit ``nexus.request.created`` so
        # downstream services see the founder's intent.
        from bsvibe_audit.events.nexus import RequestCreated  # noqa: PLC0415

        await safe_emit(
            RequestCreated(
                actor=actor_from_user(user) if user is not None else actor_system(),
                tenant_id=str(tenant_id),
                resource=resource_request(request_obj.id),
                data={
                    "project_id": str(project_id),
                    "intent_summary": request_obj.intent_summary,
                },
            ),
            session=db,
        )

    # Capture the founder's Bearer token so post-run sibling-service
    # calls (BSage index) can forward the same identity. Auto same-
    # account SSO without a separate service key.
    auth_header = request.headers.get("authorization", "") or request.headers.get("Authorization", "")
    originator_token: str | None = None
    if auth_header.lower().startswith("bearer "):
        originator_token = auth_header.split(" ", 1)[1].strip() or None
    if request_obj is not None and originator_token:
        request_obj.originator_auth = originator_token

    run_to_dispatch: uuid.UUID | None = None
    if request_obj is not None:
        seeded_run = ExecutionRun(
            tenant_id=tenant_id,
            project_id=project_id,
            request_id=request_obj.id,
            status=RunStatus.pending,
            priority=RunPriority.medium,
        )
        db.add(seeded_run)
        await db.flush()
        run_to_dispatch = seeded_run.id

        ack_msg = ConversationMessage(
            project_id=project_id,
            role="assistant",
            content=_build_ack_content(request_obj.intent_summary),
            request_id=request_obj.id,
            actions=[{"kind": "ack", "run_id": str(seeded_run.id)}],
        )
        db.add(ack_msg)

    await db.commit()
    await db.refresh(message)
    if request_obj is not None:
        await db.refresh(request_obj)

    logger.info(
        "message_sent",
        project_id=str(project_id),
        message_id=str(message.id),
        request_created=created_new,
        run_dispatched=bool(run_to_dispatch),
    )

    # SSE fan-out for the chat rail. Re-fetch the assistant rows the
    # handler may have inserted (ack) so subscribers see them without
    # waiting for a poll.
    from backend.src.core.project_events import publish_message  # noqa: PLC0415

    await publish_message(
        project_id,
        message_id=message.id,
        role=message.role,
        content=message.content,
        request_id=message.request_id,
        actions=list(message.actions or []),
        created_at=(message.created_at.isoformat() if message.created_at else ""),
    )
    if request_obj is not None:
        recent_assistant_stmt = (
            select(ConversationMessage)
            .where(
                ConversationMessage.project_id == project_id,
                ConversationMessage.role == "assistant",
                ConversationMessage.request_id == request_obj.id,
            )
            .order_by(ConversationMessage.created_at.desc())
            .limit(1)
        )
        latest = (await db.execute(recent_assistant_stmt)).scalar_one_or_none()
        if latest is not None:
            await publish_message(
                project_id,
                message_id=latest.id,
                role=latest.role,
                content=latest.content,
                request_id=latest.request_id,
                actions=list(latest.actions or []),
                created_at=(latest.created_at.isoformat() if latest.created_at else ""),
            )

    if run_to_dispatch is not None:
        stream_manager = getattr(request.app.state, "stream_manager", None)
        asyncio.create_task(_BACKGROUND_DISPATCH(run_to_dispatch, tenant_id, project_id, stream_manager))

    return SendMessageResponse(
        message=MessageResponse.model_validate(message, from_attributes=True),
        intent="request" if created_new else "chit_chat",
        request_id=request_obj.id if request_obj else None,
        request_created=created_new,
        intent_summary=(request_obj.intent_summary if request_obj else None),
    )


_dispatch_new_run = _dispatch_background
_build_adapter = build_adapter
_BACKGROUND_DISPATCH = _dispatch_new_run
