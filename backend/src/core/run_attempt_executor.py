"""G6.3 — ``dispatch_run_attempt``: drive a Request's first WorkStep
through the RunAttempt phase machine, call the resolved
:class:`ExecutorClient`, persist a Deliverable from the output, and
enqueue ``proof:queue`` for the VerifierWorker.

The phase walk is intentionally minimal for G6.3 (prepare → work →
verify → summarize → terminal with one ``execute()`` call inside
``work``). It's enough to feed the M0 measurement harness in G6.4 —
deeper tool-loop integration (per-round LLM ↔ tool turns inside
``work``) is a follow-up. Callers depend only on the
:class:`ExecutorClient` Protocol, so adding a new executor kind later
is one new class + one resolver branch (see the
``bsvibe-llm-wrapper-not-raw-litellm`` skill).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.deliverables import WorkOutputDraft, create_deliverable_from_work_output
from backend.src.core.domain import (
    DeliverableType,
    RunAttemptPhase,
    RunAttemptStatus,
    WorkStepStatus,
)
from backend.src.core.executor_config.protocol import ExecutorClient
from backend.src.core.executor_config.resolver import resolve_executor
from backend.src.core.run_attempts import (
    accept_llm_phase_output,
    advance_phase,
    create_run_attempt,
    finish_run_attempt,
)
from backend.src.core.work_steps import transition_work_step
from backend.src.models import Deliverable, Request, RunAttempt, WorkStep
from backend.src.models.executor_config import ExecutorConfig
from backend.src.queue.streams import RedisStreamManager
from backend.src.workers.verifier import PROOF_QUEUE_STREAM

logger = structlog.get_logger(__name__)


SUMMARY_PREVIEW_CHARS = 500


@dataclass(frozen=True)
class DispatchRunAttemptResult:
    """Outcome of one ``dispatch_run_attempt`` call.

    ``deliverable`` is ``None`` when the dispatch failed before any
    output could be persisted (no executor config / executor error);
    ``terminal_reason`` mirrors ``RunAttempt.terminal_reason`` so the
    caller doesn't have to refresh the row.
    """

    attempt: RunAttempt
    deliverable: Deliverable | None
    terminal_reason: str


async def dispatch_run_attempt(
    *,
    request: Request,
    work_step: WorkStep,
    tenant_id: uuid.UUID,
    session: AsyncSession,
    stream_manager: RedisStreamManager,
    executor: ExecutorClient | None = None,
    executor_kind: str | None = None,
    model: str | None = None,
) -> DispatchRunAttemptResult:
    """Drive ``work_step`` through one RunAttempt against
    ``executor`` (resolved if ``None``) and enqueue the resulting
    Deliverable on ``proof:queue``.

    Failure modes never raise — they're encoded as
    ``RunAttemptStatus.failed`` with a stable ``terminal_reason``
    string so the M0 harness can bucket by reason without try/except
    plumbing per call site.
    """
    if executor is None:
        kind, resolved_model = await _lookup_executor_config_kind_and_model(tenant_id=tenant_id, session=session)
        if kind is None:
            return await _finish_unconfigured(work_step=work_step, session=session)
        executor = await resolve_executor(tenant_id=tenant_id, session=session)
        if executor is None:
            return await _finish_unconfigured(work_step=work_step, session=session)
        executor_kind = kind
        model = resolved_model

    if executor_kind is None:
        executor_kind = "injected"

    attempt = await create_run_attempt(
        work_step=work_step,
        executor_kind=executor_kind,
        model=model,
        session=session,
    )
    await transition_work_step(step=work_step, target=WorkStepStatus.running, session=session)
    await advance_phase(attempt=attempt, target=RunAttemptPhase.work, session=session)

    metadata = {
        "tenant_id": str(tenant_id),
        "run_id": str(attempt.id),
        "request_id": str(request.id),
        "project_id": str(request.project_id),
    }
    messages = _build_messages(request=request, work_step=work_step)

    try:
        executor_result = await executor.execute(
            messages=messages,
            metadata=metadata,
            model=model or "",
        )
    except Exception as exc:
        reason = f"executor_error:{exc.__class__.__name__}"
        logger.warning(
            "dispatch_run_attempt_executor_error",
            tenant_id=str(tenant_id),
            request_id=str(request.id),
            work_step_id=str(work_step.id),
            run_attempt_id=str(attempt.id),
            error=str(exc),
        )
        await finish_run_attempt(
            attempt=attempt,
            status=RunAttemptStatus.failed,
            terminal_reason=reason,
            session=session,
        )
        await transition_work_step(step=work_step, target=WorkStepStatus.failed, session=session)
        return DispatchRunAttemptResult(attempt=attempt, deliverable=None, terminal_reason=reason)

    output_text = str(executor_result.get("output_ref") or "")
    await advance_phase(attempt=attempt, target=RunAttemptPhase.verify, session=session)
    await advance_phase(attempt=attempt, target=RunAttemptPhase.summarize, session=session)
    accept_llm_phase_output(
        attempt=attempt,
        payload={"summary": output_text[:SUMMARY_PREVIEW_CHARS]},
    )

    await finish_run_attempt(
        attempt=attempt,
        status=RunAttemptStatus.completed,
        terminal_reason="summarized",
        session=session,
    )

    deliverable = await create_deliverable_from_work_output(
        tenant_id=tenant_id,
        draft=WorkOutputDraft(
            project_id=request.project_id,
            request_id=request.id,
            work_step_id=work_step.id,
            title=work_step.name,
            summary=output_text[:SUMMARY_PREVIEW_CHARS] or None,
            type=DeliverableType.code,
            artifact_refs=[],
        ),
        session=session,
    )

    await transition_work_step(step=work_step, target=WorkStepStatus.verifying, session=session)

    await stream_manager.publish(
        PROOF_QUEUE_STREAM,
        {
            "deliverable_id": str(deliverable.id),
            "tenant_id": str(tenant_id),
        },
    )

    return DispatchRunAttemptResult(
        attempt=attempt,
        deliverable=deliverable,
        terminal_reason="summarized",
    )


async def _lookup_executor_config_kind_and_model(
    *, tenant_id: uuid.UUID, session: AsyncSession
) -> tuple[str | None, str | None]:
    """Return ``(kind, model)`` from the per-tenant
    :class:`ExecutorConfig` row, or ``(None, None)`` when no row
    exists. Pulled out so tests can patch it without standing up the
    encryption manager.
    """
    config = (
        await session.execute(select(ExecutorConfig).where(ExecutorConfig.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if config is None:
        return None, None
    return config.kind.value, config.model


async def _finish_unconfigured(*, work_step: WorkStep, session: AsyncSession) -> DispatchRunAttemptResult:
    """Record a failed RunAttempt + WorkStep transition for the
    "no per-tenant executor config" case so the audit trail shows
    *why* nothing dispatched."""
    attempt = await create_run_attempt(
        work_step=work_step,
        executor_kind="unconfigured",
        model=None,
        session=session,
    )
    await transition_work_step(step=work_step, target=WorkStepStatus.running, session=session)
    await finish_run_attempt(
        attempt=attempt,
        status=RunAttemptStatus.failed,
        terminal_reason="executor_unconfigured",
        session=session,
    )
    await transition_work_step(step=work_step, target=WorkStepStatus.failed, session=session)
    return DispatchRunAttemptResult(
        attempt=attempt,
        deliverable=None,
        terminal_reason="executor_unconfigured",
    )


def _build_messages(*, request: Request, work_step: WorkStep) -> list[dict[str, str]]:
    expected = "\n".join(f"- {item}" for item in (work_step.expected_outputs or []))
    user_block = f"Request intent:\n{request.intent}\n\nWork step: {work_step.name}\nObjective: {work_step.objective}\n"
    if expected:
        user_block += f"Expected outputs:\n{expected}\n"
    return [
        {
            "role": "system",
            "content": (
                "You are executing a single work step for an AI company. "
                "Stay focused on the objective and produce a concrete output the "
                "founder can verify."
            ),
        },
        {"role": "user", "content": user_block},
    ]
