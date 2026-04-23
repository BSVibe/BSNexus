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

import asyncio
import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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

        knowledge = resolve_knowledge_client(integrations.bsage)
        audit = resolve_audit_sink(integrations.bsupervisor)

        tools_available = _tools_from_executor_hint(executor)
        composition = await self._assembler.compose(
            run,
            knowledge,
            intent_summary=request.intent_summary,
            tools_available=tools_available,
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
        await db.flush()

        if executor is None:
            # Caller will invoke the executor and call ``on_run_completed``
            # when done. Orchestrator role ends here for this run.
            emit_post_async(audit, run, {"status": "running", "stage": "ready"})
            return run

        try:
            result = await executor.execute(
                composition.system_prompt,
                tools_allowed=composition.tools_allowed,
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
        """Finalize a completed run + enqueue children whose deps are met."""
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

        children = await _find_ready_children(db, run)
        for child in children:
            await self._state.transition(
                child,
                RunStatus.pending,
                reason=f"dependencies met (parent {run.id})",
                actor="orchestrator",
                db_session=db,
                stream_manager=stream_manager,
            )


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
        source=(
            CompositionSource.bsage
            if composition.source == "bsage"
            else CompositionSource.local
        ),
        system_prompt_ref={"inline": composition.system_prompt},
        tools_allowed=list(composition.tools_allowed),
        context_doc_refs=list(composition.context_doc_refs),
        persona_label=composition.persona_label,
        fit_score=composition.fit_score,
    )
    db.add(snapshot)
    await db.flush()
    return snapshot


async def _find_ready_children(
    db: AsyncSession, parent: ExecutionRun
) -> list[ExecutionRun]:
    """Find blocked/pending children whose all dependencies are now done."""
    deps = execution_run_dependencies
    dependent_ids_stmt = select(deps.c.run_id).where(deps.c.dependency_id == parent.id)
    result = await db.execute(dependent_ids_stmt)
    dependent_ids = [row[0] for row in result.all()]
    if not dependent_ids:
        return []

    # For each candidate, confirm all of its dependencies are done.
    candidates_stmt = select(ExecutionRun).where(
        ExecutionRun.id.in_(dependent_ids),
        ExecutionRun.status == RunStatus.blocked,
    )
    result = await db.execute(candidates_stmt)
    candidates = list(result.scalars())

    ready: list[ExecutionRun] = []
    for candidate in candidates:
        deps_stmt = select(ExecutionRun.status).join(
            deps, deps.c.dependency_id == ExecutionRun.id
        ).where(deps.c.run_id == candidate.id)
        dep_statuses = (await db.execute(deps_stmt)).scalars().all()
        if all(status == RunStatus.done for status in dep_statuses):
            ready.append(candidate)
    return ready


_singleton: RunOrchestrator | None = None


def get_run_orchestrator() -> RunOrchestrator:
    global _singleton
    if _singleton is None:
        _singleton = RunOrchestrator()
    return _singleton
