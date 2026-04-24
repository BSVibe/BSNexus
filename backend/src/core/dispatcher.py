"""Fire-and-forget run dispatch helper.

Spawns a background task that runs ``RunOrchestrator.dispatch_run`` on
its own DB session so the caller (an HTTP handler, or the orchestrator
itself after completing the previous phase) returns immediately.

Extracted from ``api/conversation.py`` so the orchestrator can enqueue
its own successors without creating an import cycle.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.orchestrator_adapter import LiteLLMOrchestratorAdapter
from backend.src.core.run_artifacts import publish_run_output
from backend.src.core.run_orchestrator import get_run_orchestrator
from backend.src.core.worker_adapter import WorkerDispatchAdapter
from backend.src.core.worker_dispatch import WorkerDispatcher
from backend.src.models import ExecutorConfig, RunStatus
from backend.src.storage.database import async_session

logger = structlog.get_logger(__name__)


def fire_run(
    run_id: uuid.UUID,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    stream_manager: Any | None,
) -> asyncio.Task:
    """Schedule ``_dispatch_background`` as a new task and return it.

    Callers typically ignore the returned task — the background work
    finalizes the run in its own session + commits independently.
    """
    return asyncio.create_task(
        _dispatch_background(run_id, tenant_id, project_id, stream_manager)
    )


async def _dispatch_background(
    run_id: uuid.UUID,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    stream_manager: Any | None,
) -> None:
    try:
        async with async_session() as session:
            adapter = await build_adapter(
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
            if run is not None and run.status == RunStatus.done:
                await publish_run_output(run, session)
            await session.commit()
    except Exception:
        logger.exception("background_dispatch_failed", run_id=str(run_id))


async def build_adapter(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    run_id: uuid.UUID,
    project_id: uuid.UUID,
    stream_manager: Any | None = None,
) -> Any | None:
    """Pick an executor adapter for the tenant's default ExecutorConfig.

    Mirrors ``api/conversation._build_adapter``. Kept here too so the
    orchestrator's successor-dispatch path doesn't re-import the
    HTTP-facing module.
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
        return None

    exec_type = (row.executor_type or "").lower()
    cfg = row.config or {}

    if exec_type == "generic_llm":
        model = cfg.get("model")
        if not model:
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
            return None
        return LiteLLMOrchestratorAdapter(
            model=cfg.get("model") or "openai/gpt-4o-mini",
            project_id=project_id,
            api_key=cfg.get("bsgateway_api_key") or "unused",
            base_url=gateway_url,
        )

    if exec_type in {"worker", "claude_code", "codex"}:
        if stream_manager is None:
            return None
        required = None if exec_type == "worker" else [exec_type]
        dispatcher = WorkerDispatcher(stream_manager)
        worker = await dispatcher.find_available_worker(
            session, tenant_id=tenant_id, required_capabilities=required
        )
        if worker is None:
            return None
        return WorkerDispatchAdapter(
            stream_manager=stream_manager,
            worker_id=worker.id,
            run_id=run_id,
            project_id=project_id,
        )

    return None
