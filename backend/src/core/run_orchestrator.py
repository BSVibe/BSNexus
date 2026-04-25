"""RunOrchestrator — event-driven dispatch for a single ExecutionRun.

Replaces the old GlobalDispatcher (5-second polling) with an
event-driven model: new requests / run completions fire
``dispatch_run``; completed parents enqueue their children via
``on_run_completed``.

The orchestration flow per run:

    1. Load tenant integration config (cached, 60s TTL).
    2. Resolve KnowledgeClient (BSage or Noop).
    3. Resolve AuditSink (BSupervisor or Noop).
    4. PromptAssembler composes system prompt from knowledge fragments.
    5. Persist CompositionSnapshot; link to run.
    6. Sync preflight via BSupervisor (200ms timeout, fail-open).
    7. State machine: pending → running.
    8. Hand off to executor (LiteLLM + BSGateway hook, or claude_code).
    9. Record output; fire-and-forget post-audit.
    10. State machine: running → done or blocked.
    11. Enqueue children whose dependencies are met.

This module does NOT busy-loop. Call ``dispatch_run`` inline from
request creation or ``on_run_completed`` from a completion handler.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core import harness
from backend.src.core.audit import AuditSink, emit_post_async, resolve_audit_sink
from backend.src.core.composer import (
    PromptAssembler,
    default_template_registry,
    resolve_knowledge_client,
)
from backend.src.core.integrations import get_tenant_integration_snapshot
from backend.src.core.state_machine import RunStateMachine
from backend.src.models import (
    CompositionSnapshot,
    CompositionSource,
    ConversationMessage,
    ExecutionRun,
    Request,
    RunStatus,
    execution_run_dependencies,
)
from backend.src.queue.streams import RedisStreamManager

logger = structlog.get_logger(__name__)


class RunOrchestrator:
    """Single-run orchestrator. Stateless; safe to share as singleton."""

    def __init__(
        self,
        *,
        assembler: PromptAssembler | None = None,
        state_machine: RunStateMachine | None = None,
    ):
        self._assembler = assembler or PromptAssembler(default_template_registry())
        self._state = state_machine or RunStateMachine()

    async def dispatch_run(
        self,
        run_id: uuid.UUID,
        *,
        db: AsyncSession,
        stream_manager: RedisStreamManager | None = None,
        executor: Any | None = None,
    ) -> ExecutionRun:
        """Dispatch one run through compose → audit → execute → audit-post.

        ``executor`` is the LLM/tool-call executor (built via
        ``core.executor.factory.get_executor`` — left injectable for tests).
        When None, the run stops at "ready to execute" and stays in
        ``running`` state until the executor callback lands. This keeps
        the orchestrator testable without a full LLM loop.
        """
        run = await _load_run(db, run_id)
        request = await _load_request(db, run.request_id)

        snapshot_data = get_tenant_integration_snapshot
        integrations = await snapshot_data(db, run.tenant_id)

        knowledge = resolve_knowledge_client(integrations.bsage, auth_token=request.originator_auth)
        audit = resolve_audit_sink(integrations.bsupervisor, auth_token=request.originator_auth)

        # Refresh .bsnexus/context/*.md so the composer's pointer to
        # those files resolves to fresh state. Failing to refresh must
        # not break the run — log and continue with stale context.
        try:
            await harness.refresh_context(run.project_id, request=request, db=db)
        except Exception:  # noqa: BLE001
            logger.exception("harness_refresh_failed", run_id=str(run.id))

        tools_available = _tools_from_executor_hint(executor)
        workspace_state = _safe_workspace_listing(run.project_id)
        prior_iterations = await _load_prior_iterations_for_compose(db, run)
        composition = await self._assembler.compose(
            run,
            knowledge,
            intent_summary=request.intent_summary,
            tools_available=tools_available,
            workspace_state=workspace_state,
            prior_iterations=prior_iterations,
        )
        snapshot = await _persist_snapshot(db, run, request, composition)
        run.composition_snapshot_id = snapshot.id

        audit_result = await audit.preflight(run, snapshot)
        if audit_result.blocked:
            return await self._state.transition(
                run,
                RunStatus.blocked,
                reason=audit_result.reason or "blocked by audit preflight",
                actor="audit",
                db_session=db,
                stream_manager=stream_manager,
            )

        await self._state.transition(
            run,
            RunStatus.running,
            actor="orchestrator",
            db_session=db,
            stream_manager=stream_manager,
        )
        # Commit BEFORE the long-running LLM call so the execution_run /
        # composition_snapshot / message rows aren't held under a row
        # lock for the entire duration. Without this, ``DELETE FROM
        # projects`` — which cascades to execution_runs — blocks for
        # minutes while waiting for this transaction to finish.
        await db.commit()

        if executor is None:
            # Caller will invoke the executor and call ``on_run_completed``
            # when done. Orchestrator role ends here for this run.
            emit_post_async(audit, run, {"status": "running", "stage": "ready"})
            return run

        history = await _load_chat_history(db, project_id=run.project_id, origin_message_id=request.origin_message_id)
        user_prompt = run.directive or request.intent_summary

        try:
            result = await executor.execute(
                composition.system_prompt,
                user_prompt,
                tools_allowed=composition.tools_allowed,
                history=history,
            )
        except Exception as exc:  # noqa: BLE001 — sink-all at the executor boundary
            logger.exception("run_executor_failed", run_id=str(run.id))
            emit_post_async(audit, run, {"status": "error", "error": str(exc)})
            return await self._state.transition(
                run,
                RunStatus.blocked,
                reason=f"executor failed: {exc}",
                actor="orchestrator",
                db_session=db,
                stream_manager=stream_manager,
            )

        # Async executors (e.g. remote workers) return a "dispatched"
        # sentinel — the run stays in ``running`` state and a separate
        # result-consumer finalises it once the worker reports back. Do
        # not call on_run_completed here or we'd mark it done prematurely.
        if isinstance(result, dict) and result.get("status") == "dispatched":
            emit_post_async(audit, run, result)
            logger.info(
                "run_dispatched_awaiting_worker",
                run_id=str(run.id),
                stream_msg_id=result.get("stream_msg_id"),
                worker_id=result.get("worker_id"),
            )
            return run

        await self.on_run_completed(
            run,
            result=result,
            audit=audit,
            db=db,
            stream_manager=stream_manager,
        )
        return run

    async def on_run_completed(
        self,
        run: ExecutionRun,
        *,
        result: Any,
        audit: AuditSink,
        db: AsyncSession,
        stream_manager: RedisStreamManager | None = None,
    ) -> None:
        """Finalize a completed run, then seed the next iteration.

        Iterative replanner model: each completed run kicks off a fresh
        ``ExecutionRun`` (pending, parent_run_id = this run) that the
        background dispatcher will pick up. Phase 0 of that dispatcher
        calls the replanner to decide whether to do another iteration,
        declare the goal done, or ask the founder a question.

        We do NOT decide here whether the chain is done — that's the
        replanner's call when it sees the latest results. From this
        function's POV it just always schedules the next replanner pass.
        """
        if isinstance(result, dict):
            run.output_type = result.get("output_type")
            run.output_ref = result.get("output_ref")
            run.actual_cost_cents = int(result.get("actual_cost_cents", 0) or 0)

        emit_post_async(audit, run, result if isinstance(result, dict) else {"status": "done"})

        await self._state.transition(
            run,
            RunStatus.done,
            actor="orchestrator",
            db_session=db,
            stream_manager=stream_manager,
        )

        if run.request_id is None:
            return

        # Schedule the next iteration. The replanner inside Phase 0 of
        # the dispatcher decides whether this is the last one (it can
        # return ``done`` and the new run becomes a no-op closer).
        next_run = ExecutionRun(
            tenant_id=run.tenant_id,
            project_id=run.project_id,
            request_id=run.request_id,
            parent_run_id=run.id,
            status=RunStatus.pending,
            priority=run.priority,
        )
        db.add(next_run)
        await db.flush()
        # Commit so the background task's own session sees the row.
        await db.commit()
        _fire_async(next_run.id, next_run.tenant_id, next_run.project_id, stream_manager)


def _fire_async(
    run_id: uuid.UUID,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    stream_manager: Any | None,
) -> None:
    """Schedule a background dispatch of ``run_id``.

    Lazy import of ``core.dispatcher`` avoids a circular import: the
    dispatcher module depends on the orchestrator to run the actual
    compose→audit→execute loop.
    """
    from backend.src.core.dispatcher import fire_run  # noqa: PLC0415

    fire_run(run_id, tenant_id, project_id, stream_manager)


async def _find_blocked_successor(db: AsyncSession, parent_run_id: uuid.UUID) -> ExecutionRun | None:
    stmt = (
        select(ExecutionRun)
        .where(
            ExecutionRun.parent_run_id == parent_run_id,
            ExecutionRun.status == RunStatus.blocked,
        )
        .order_by(ExecutionRun.created_at.asc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


def _safe_workspace_listing(project_id: uuid.UUID) -> list[dict]:
    """Workspace files for the prompt — never raises, returns ``[]`` on
    any error so a transient FS hiccup doesn't kill the run.
    """
    try:
        from backend.src.core import project_workspace  # noqa: PLC0415

        return list(project_workspace.list_files(project_id))
    except Exception:  # noqa: BLE001
        logger.exception("workspace_listing_failed", project_id=str(project_id))
        return []


async def _load_prior_iterations_for_compose(db: AsyncSession, current_run: ExecutionRun) -> list[dict]:
    """Build a per-iteration summary list for the prompt.

    Pulls every previously-completed run on the same request (excluding
    ``current_run`` itself), in chronological order. Each entry carries
    the worker's ``founder_summary`` (or ``inline``) plus the list of
    file paths it wrote. The worker prompt uses this to refuse to
    re-create files already produced.
    """
    if current_run.request_id is None:
        return []
    stmt = (
        select(ExecutionRun)
        .where(
            ExecutionRun.request_id == current_run.request_id,
            ExecutionRun.id != current_run.id,
            ExecutionRun.status == RunStatus.done,
        )
        .order_by(ExecutionRun.created_at.asc())
    )
    rows = list((await db.execute(stmt)).scalars())
    out: list[dict] = []
    for r in rows:
        out_ref = r.output_ref if isinstance(r.output_ref, dict) else {}
        files_written = []
        for f in (out_ref.get("files") or [])[:50]:
            if isinstance(f, dict) and f.get("path"):
                files_written.append(str(f["path"]))
        out.append(
            {
                "name": _name_from_directive(r.directive),
                "founder_summary": str(out_ref.get("founder_summary") or out_ref.get("inline") or "")[:600],
                "files_written": files_written,
            }
        )
    return out


def _name_from_directive(directive: str | None) -> str:
    if not directive:
        return "iteration"
    first_line = directive.strip().splitlines()[0]
    return first_line[:48] or "iteration"


def _tools_from_executor_hint(executor: Any | None) -> list[str]:
    if executor is None:
        return []
    # Executors may expose supported tool names. Default to a conservative
    # set so the template picker still works.
    return getattr(executor, "tools_supported", ["read", "write"])


async def _load_run(db: AsyncSession, run_id: uuid.UUID) -> ExecutionRun:
    result = await db.execute(select(ExecutionRun).where(ExecutionRun.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise LookupError(f"ExecutionRun {run_id} not found")
    return run


async def _load_request(db: AsyncSession, request_id: uuid.UUID) -> Request:
    result = await db.execute(select(Request).where(Request.id == request_id))
    req = result.scalar_one_or_none()
    if req is None:
        raise LookupError(f"Request {request_id} not found")
    return req


async def _load_chat_history(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    origin_message_id: uuid.UUID | None,
    limit: int = 20,
) -> list[dict[str, str]]:
    """Return prior chat exchanges so multi-turn directions ("now do X
    to what you just produced") can reference earlier work.

    Excludes the message that originated the current request — that
    message content is already the ``user_prompt`` we pass to the
    executor, so including it here would double-send it.
    """
    stmt = (
        select(ConversationMessage)
        .where(ConversationMessage.project_id == project_id)
        .order_by(ConversationMessage.created_at.asc())
    )
    rows = list((await db.execute(stmt)).scalars())
    history: list[dict[str, str]] = []
    for m in rows:
        if origin_message_id is not None and m.id == origin_message_id:
            break
        if m.role not in ("user", "assistant"):
            continue
        history.append({"role": m.role, "content": m.content})
    # Keep the tail — recent context matters most.
    if len(history) > limit:
        history = history[-limit:]
    return history


async def _persist_snapshot(
    db: AsyncSession,
    run: ExecutionRun,
    request: Request,
    composition: Any,
) -> CompositionSnapshot:
    snapshot = CompositionSnapshot(
        tenant_id=run.tenant_id,
        request_id=request.id,
        execution_run_id=run.id,
        source=(CompositionSource.bsage if composition.source == "bsage" else CompositionSource.local),
        system_prompt_ref={"inline": composition.system_prompt},
        tools_allowed=list(composition.tools_allowed),
        context_doc_refs=list(composition.context_doc_refs),
        persona_label=composition.persona_label,
        fit_score=composition.fit_score,
    )
    db.add(snapshot)
    await db.flush()
    return snapshot


async def _find_ready_children(db: AsyncSession, parent: ExecutionRun) -> list[ExecutionRun]:
    """Find blocked/pending children whose all dependencies are now done.

    Batch-loaded (S2-1 M2): the per-candidate dep-status loop has been
    replaced with a single grouped query joining the dependencies
    association table to ``ExecutionRun.status``. Total round-trips per
    call: ``3`` regardless of candidate count (was ``2 + N``).
    """
    deps = execution_run_dependencies
    dependent_ids_stmt = select(deps.c.run_id).where(deps.c.dependency_id == parent.id)
    result = await db.execute(dependent_ids_stmt)
    dependent_ids = [row[0] for row in result.all()]
    if not dependent_ids:
        return []

    candidates_stmt = select(ExecutionRun).where(
        ExecutionRun.id.in_(dependent_ids),
        ExecutionRun.status == RunStatus.blocked,
    )
    candidates = list((await db.execute(candidates_stmt)).scalars())
    if not candidates:
        return []

    # One grouped query: for every (candidate_run_id, dep_status) pair,
    # count rows where dep_status != done. Candidates with zero such
    # rows are ready. Avoids the per-candidate query (N+1).
    candidate_ids = [c.id for c in candidates]
    pending_stmt = (
        select(deps.c.run_id)
        .join(ExecutionRun, ExecutionRun.id == deps.c.dependency_id)
        .where(deps.c.run_id.in_(candidate_ids))
        .where(ExecutionRun.status != RunStatus.done)
        .distinct()
    )
    blocked_ids = {row[0] for row in (await db.execute(pending_stmt)).all()}
    return [c for c in candidates if c.id not in blocked_ids]


_singleton: RunOrchestrator | None = None


def get_run_orchestrator() -> RunOrchestrator:
    global _singleton
    if _singleton is None:
        _singleton = RunOrchestrator()
    return _singleton
