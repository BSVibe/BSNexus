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
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.auth import get_current_user
from backend.src.core.orchestrator_adapter import LiteLLMOrchestratorAdapter
from backend.src.core.planner import maybe_plan_phases, seed_phase_chain
from backend.src.core.request_extractor import RequestExtractor
from backend.src.core.run_artifacts import publish_run_output
from backend.src.core.run_orchestrator import get_run_orchestrator
from backend.src.core.tenant_context import get_tenant_id
from backend.src.core.worker_adapter import WorkerDispatchAdapter
from backend.src.core.worker_dispatch import WorkerDispatcher
from backend.src.models import (
    ConversationMessage,
    ExecutionRun,
    ExecutorConfig,
    Project,
    RunPriority,
    RunStatus,
)
from backend.src.schemas import MessageCreate, MessageResponse, SendMessageResponse
from backend.src.storage.database import async_session, get_db

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
    outcome = await extractor.process_message(
        message, tenant_id=tenant_id, db=db
    )

    # Chit-chat skips the orchestrator; question / request / modification
    # all seed a run. For macro directions ("앱 만들어줘"), the planner
    # asks the tenant's LLM for a phase plan and seeds a linear chain
    # BEFORE we dispatch so the orchestrator runs phase 1 first and its
    # completion hook fires phase 2, etc.
    run_to_dispatch: uuid.UUID | None = None
    if outcome.request is not None:
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

        plan = await maybe_plan_phases(
            direction=outcome.request.intent_summary,
            tenant_id=tenant_id,
            session=db,
        )
        if plan is not None:
            await seed_phase_chain(
                session=db, root_run=seeded_run, plan=plan
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
        run_dispatched=bool(run_to_dispatch),
    )

    if run_to_dispatch is not None:
        stream_manager = getattr(request.app.state, "stream_manager", None)
        asyncio.create_task(
            _BACKGROUND_DISPATCH(run_to_dispatch, tenant_id, project_id, stream_manager)
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


# Module-level hook so tests can swap the background dispatcher with a
# no-op that doesn't touch the real DB / LLM / Redis.
async def _dispatch_new_run(
    run_id: uuid.UUID,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    stream_manager: Any | None,
) -> None:
    """Kick off RunOrchestrator in the background.

    Runs on its own session so the HTTP request returns immediately.
    Errors are logged but never raised out — orchestration failures land
    the run in ``blocked`` state via the state machine, not as API
    errors.
    """
    try:
        async with async_session() as session:
            adapter = await _build_adapter(
                session,
                tenant_id,
                run_id=run_id,
                project_id=project_id,
                stream_manager=stream_manager,
            )
            run = await get_run_orchestrator().dispatch_run(
                run_id,
                db=session,
                executor=adapter,
                stream_manager=stream_manager,
            )
            # Sync executor paths (generic_llm / bsgateway) land the run
            # in ``done`` right here; worker paths return ``running`` and
            # WorkerResultConsumer publishes artifacts later. Calling the
            # helper is idempotent so the sync path runs it now.
            if run is not None and run.status == RunStatus.done:
                await publish_run_output(run, session)
            await session.commit()
    except Exception:
        logger.exception("background_dispatch_failed", run_id=str(run_id))


async def _build_adapter(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    run_id: uuid.UUID,
    project_id: uuid.UUID,
    stream_manager: Any | None = None,
) -> Any | None:
    """Pick an executor for the tenant's default ``ExecutorConfig``.

    Every LLM call that BSNexus initiates on behalf of a tenant must be
    gated by the tenant's own ``ExecutorConfig``. A tenant that opts
    into remote-worker execution (``executor_type="worker"``) — or any
    non-LLM backend — must never trigger an API call from the
    backend: the API bill would be on us, not them. So this function
    only returns a live LLM adapter for ``generic_llm`` and
    ``bsgateway`` configs (where the tenant supplied their own credentials);
    everything else returns ``None`` and the run pauses at ``running``
    state for an out-of-band executor to pick up.
    """
    row = (
        await session.execute(
            select(ExecutorConfig).where(
                ExecutorConfig.tenant_id == tenant_id,
                ExecutorConfig.is_selected.is_(True),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        logger.info("dispatch_no_default_executor", tenant_id=str(tenant_id))
        return None

    exec_type = (row.executor_type or "").lower()
    cfg = row.config or {}

    if exec_type == "generic_llm":
        model = cfg.get("model")
        if not model:
            logger.warning(
                "dispatch_generic_llm_missing_model",
                config_id=str(row.id),
                tenant_id=str(tenant_id),
            )
            return None
        return LiteLLMOrchestratorAdapter(
            model=model,
            project_id=project_id,
            api_key=cfg.get("api_key") or "unused",
            base_url=cfg.get("base_url"),
        )

    if exec_type == "bsgateway":
        gateway_url = cfg.get("bsgateway_url")
        if not gateway_url:
            logger.warning(
                "dispatch_bsgateway_missing_url",
                config_id=str(row.id),
                tenant_id=str(tenant_id),
            )
            return None
        return LiteLLMOrchestratorAdapter(
            model=cfg.get("model") or "openai/gpt-4o-mini",
            project_id=project_id,
            api_key=cfg.get("bsgateway_api_key") or "unused",
            base_url=gateway_url,
        )

    # "worker" is the generic remote-execution case; "claude_code" and
    # "codex" are specializations that require the worker to advertise
    # that specific CLI capability. All three paths dispatch through
    # WorkerDispatchAdapter; the only difference is the capability
    # filter used to pick a matching worker. The CLI itself is fixed
    # at worker registration/startup — not negotiated per task.
    if exec_type in {"worker", "claude_code", "codex"}:
        if stream_manager is None:
            logger.warning(
                "dispatch_worker_missing_stream_manager",
                tenant_id=str(tenant_id),
                executor_type=exec_type,
            )
            return None
        required_capabilities: list[str] | None = (
            None if exec_type == "worker" else [exec_type]
        )
        dispatcher = WorkerDispatcher(stream_manager)
        worker = await dispatcher.find_available_worker(
            session,
            tenant_id=tenant_id,
            required_capabilities=required_capabilities,
        )
        if worker is None:
            logger.info(
                "dispatch_worker_no_match",
                tenant_id=str(tenant_id),
                executor_type=exec_type,
                required_capabilities=required_capabilities,
            )
            return None
        return WorkerDispatchAdapter(
            stream_manager=stream_manager,
            worker_id=worker.id,
            run_id=run_id,
            project_id=project_id,
        )

    logger.info(
        "dispatch_executor_type_unknown",
        executor_type=exec_type,
        config_id=str(row.id),
        tenant_id=str(tenant_id),
    )
    return None


_BACKGROUND_DISPATCH = _dispatch_new_run
