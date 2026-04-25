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

from backend.src.core.composer import resolve_knowledge_client
from backend.src.core.integrations import get_tenant_integration_snapshot
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
    return asyncio.create_task(_dispatch_background(run_id, tenant_id, project_id, stream_manager))


async def _dispatch_background(
    run_id: uuid.UUID,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    stream_manager: Any | None,
) -> None:
    """Run dispatch across three session scopes so the DB pool isn't
    pinned for the full LLM round-trip.

    Phase 1 (short session): build adapter + run ``dispatch_run`` with
    ``executor=None`` so the orchestrator only does compose →
    snapshot → state transition + COMMIT, then returns. The session is
    released back to the pool before the network call.

    Phase 2 (no session): call ``adapter.execute(...)`` for the actual
    LLM turn. This is the minutes-long step; holding a DB connection
    here would starve concurrent HTTP requests (especially DELETE
    project, which blocks behind the row lock).

    Phase 3 (short session): re-attach the run in a fresh session and
    finalize via ``on_run_completed`` → ``publish_run_output``.
    """
    from backend.src.core.audit import resolve_audit_sink  # noqa: PLC0415 — avoid cycle
    from backend.src.core.planner import replan_next_step  # noqa: PLC0415

    try:
        # Phase 0: replan. Calls the chief-of-staff LLM to decide what
        # this iteration should do. Possible outcomes:
        #   next_step → fill in run.directive, write a phase_start
        #               message, fall through to Phase 1.
        #   done      → mark the run done with no work, write a
        #               chain_done message, exit.
        #   ask_founder → create a Decision row + decision_request
        #                 message, mark run blocked, exit.
        async with async_session() as session:
            from backend.src.core.run_artifacts import (  # noqa: PLC0415
                insert_chat_event,
            )
            from backend.src.models import Decision  # noqa: PLC0415
            from backend.src.models import ExecutionRun as _ExecutionRun  # noqa: PLC0415
            from backend.src.models import Request as _Request  # noqa: PLC0415

            run_row = (
                await session.execute(select(_ExecutionRun).where(_ExecutionRun.id == run_id))
            ).scalar_one_or_none()
            if run_row is None:
                return
            if run_row.request_id is None:
                return

            req_row = (
                await session.execute(select(_Request).where(_Request.id == run_row.request_id))
            ).scalar_one_or_none()
            if req_row is None:
                return

            prior_runs = await _load_prior_completed_runs(session, req_row.id)
            pending_decisions = await _load_pending_decisions(session, req_row.id)
            recent_msgs = await _load_recent_messages(session, run_row.project_id)

            replan = await replan_next_step(
                request=req_row,
                completed_runs=prior_runs,
                pending_decisions=pending_decisions,
                recent_messages=recent_msgs,
                tenant_id=tenant_id,
                session=session,
            )

            if replan.decision == "done":
                await insert_chat_event(
                    session,
                    project_id=run_row.project_id,
                    request_id=req_row.id,
                    run_id=run_row.id,
                    kind="chain_done",
                    content=replan.founder_message,
                )
                from backend.src.models import RequestStatus  # noqa: PLC0415

                run_row.status = RunStatus.done
                req_row.status = RequestStatus.completed
                await session.commit()
                return

            if replan.decision == "ask_founder":
                decision = Decision(
                    tenant_id=tenant_id,
                    project_id=run_row.project_id,
                    request_id=req_row.id,
                    origin_run_id=run_row.id,
                    question=replan.question or "",
                    options=replan.options or [],
                    blocking=replan.blocking,
                )
                session.add(decision)
                await session.flush()
                await insert_chat_event(
                    session,
                    project_id=run_row.project_id,
                    request_id=req_row.id,
                    run_id=run_row.id,
                    kind="decision_request",
                    content=replan.founder_message,
                    extra={
                        "question": replan.question,
                        "options": replan.options or [],
                        "decision_id": str(decision.id),
                    },
                )
                run_row.status = RunStatus.blocked
                run_row.error_message = "awaiting founder decision"
                await session.commit()
                return

            # decision == "next_step"
            run_row.directive = replan.phase_direction
            await insert_chat_event(
                session,
                project_id=run_row.project_id,
                request_id=req_row.id,
                run_id=run_row.id,
                kind="phase_start",
                content=replan.founder_message,
                extra={"phase_name": replan.phase_name or "iteration"},
            )
            await session.commit()

        # Phase 1: prepare + transition to running, commit, release.
        prepared: dict[str, Any] | None = None
        async with async_session() as session:
            adapter = await build_adapter(
                session,
                tenant_id,
                run_id=run_id,
                project_id=project_id,
                stream_manager=stream_manager,
            )
            if adapter is None:
                # No executor configured — orchestrator's normal path
                # (with executor=None) still transitions to running and
                # awaits an external callback.
                await get_run_orchestrator().dispatch_run(
                    run_id,
                    db=session,
                    executor=None,
                    stream_manager=stream_manager,
                )
                return

            run = await get_run_orchestrator().dispatch_run(
                run_id,
                db=session,
                executor=None,  # prep-only; we run the LLM ourselves below
                stream_manager=stream_manager,
            )
            if run is None or run.status != RunStatus.running:
                # Blocked by audit, etc. — nothing to finalize.
                return

            # Gather everything the LLM needs before closing the session.
            from backend.src.core.run_orchestrator import (  # noqa: PLC0415
                _load_chat_history,
                _load_request,
            )
            from backend.src.models import CompositionSnapshot  # noqa: PLC0415

            request = await _load_request(session, run.request_id)
            history = await _load_chat_history(
                session,
                project_id=run.project_id,
                origin_message_id=request.origin_message_id,
            )
            snapshot_row = (
                await session.execute(
                    select(CompositionSnapshot).where(CompositionSnapshot.id == run.composition_snapshot_id)
                )
            ).scalar_one()
            prepared = {
                "adapter": adapter,
                "system_prompt": (snapshot_row.system_prompt_ref or {}).get("inline", ""),
                "user_prompt": run.directive or request.intent_summary,
                "tools_allowed": list(snapshot_row.tools_allowed or []),
                "history": history,
            }

        # Phase 2: run the LLM with no DB session held.
        assert prepared is not None
        adapter = prepared["adapter"]
        try:
            result = await adapter.execute(
                prepared["system_prompt"],
                prepared["user_prompt"],
                tools_allowed=prepared["tools_allowed"],
                history=prepared["history"],
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("llm_execute_failed", run_id=str(run_id))
            result = {"_error": str(exc)}

        # Async / worker executors return a "dispatched" sentinel —
        # they'll finalize via the worker-result consumer, not here.
        if isinstance(result, dict) and result.get("status") == "dispatched":
            return

        # Phase 3: finalize in a fresh session.
        async with async_session() as session:
            integrations = await get_tenant_integration_snapshot(session, tenant_id)
            audit = resolve_audit_sink(integrations.bsupervisor)
            # Re-load the run — ORM object from phase 1 is detached.
            from backend.src.models import ExecutionRun  # noqa: PLC0415

            run = (await session.execute(select(ExecutionRun).where(ExecutionRun.id == run_id))).scalar_one_or_none()
            if run is None:
                # Project was deleted while the LLM was running — nothing
                # to finalize. Not an error.
                logger.info("run_disappeared_during_llm", run_id=str(run_id))
                return

            if isinstance(result, dict) and "_error" in result:
                from backend.src.models import RunStatus as _RunStatus  # noqa: PLC0415

                run.status = _RunStatus.blocked
                run.error_message = result["_error"]
                await session.commit()
                return

            await get_run_orchestrator().on_run_completed(
                run,
                result=result,
                audit=audit,
                db=session,
                stream_manager=stream_manager,
            )

            if run.status == RunStatus.done:
                originator_token: str | None = None
                if run.request_id is not None:
                    from backend.src.models import Request as _Request  # noqa: PLC0415

                    req_row = (
                        await session.execute(select(_Request).where(_Request.id == run.request_id))
                    ).scalar_one_or_none()
                    if req_row is not None:
                        originator_token = req_row.originator_auth
                knowledge = resolve_knowledge_client(integrations.bsage, auth_token=originator_token)
                await publish_run_output(run, session, knowledge=knowledge)
            await session.commit()
    except Exception:
        logger.exception("background_dispatch_failed", run_id=str(run_id))


async def _load_prior_completed_runs(session: AsyncSession, request_id: uuid.UUID) -> list[Any]:
    """Ordered prior completed runs for a request — feeds the replanner."""
    from backend.src.models import ExecutionRun as _ExecutionRun  # noqa: PLC0415

    rows = (
        (
            await session.execute(
                select(_ExecutionRun)
                .where(
                    _ExecutionRun.request_id == request_id,
                    _ExecutionRun.status == RunStatus.done,
                )
                .order_by(_ExecutionRun.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def _load_pending_decisions(session: AsyncSession, request_id: uuid.UUID) -> list[Any]:
    from backend.src.models import Decision  # noqa: PLC0415

    rows = (
        (
            await session.execute(
                select(Decision)
                .where(Decision.request_id == request_id, Decision.resolved_at.is_(None))
                .order_by(Decision.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def _load_recent_messages(session: AsyncSession, project_id: uuid.UUID, *, limit: int = 12) -> list[Any]:
    from backend.src.models import ConversationMessage  # noqa: PLC0415

    rows = (
        (
            await session.execute(
                select(ConversationMessage)
                .where(ConversationMessage.project_id == project_id)
                .order_by(ConversationMessage.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return list(reversed(rows))


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
            select(ExecutorConfig)
            .where(
                ExecutorConfig.tenant_id == tenant_id,
                ExecutorConfig.is_selected.is_(True),
            )
            .order_by(ExecutorConfig.created_at.desc())
            .limit(1)
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
        worker = await dispatcher.find_available_worker(session, tenant_id=tenant_id, required_capabilities=required)
        if worker is None:
            return None
        return WorkerDispatchAdapter(
            stream_manager=stream_manager,
            worker_id=worker.id,
            run_id=run_id,
            project_id=project_id,
        )

    return None
