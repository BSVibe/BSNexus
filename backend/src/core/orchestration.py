"""Production run orchestration (G9).

The greenfield rebuild (G0-G8) built every component of the
Direction → … → PR pipeline and proved them via the M0 benchmark
bridge + unit tests, but never wired the *autonomous production loop*.
G9 closes that:

  - Front half: ``RequestWorker`` (``workers/request_worker.py``)
    consumes ``request:queue``, calls ``plan_and_dispatch_request``.
  - Back half: ``advance_request_after_proof`` (here) is called by the
    VerifierWorker after a Deliverable's proof resolves — it walks the
    WorkStep + Request state machines forward so a fully-verified
    Request reaches ``shipped`` (which fires the G8.3 PR hook).

This module holds the pure orchestration helpers; the workers are the
thin Redis-consumer shells around them.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.config import settings as app_settings
from backend.src.core.domain import (
    ProofState,
    RequestStatus,
    WorkPlanCreatedBy,
    WorkStepStatus,
)
from backend.src.core.executor_config.protocol import ExecutorClient
from backend.src.core.planning import build_project_context, decompose_request
from backend.src.core.run_attempt_executor import (
    _lookup_executor_config_kind_and_model,
    dispatch_run_attempt,
)
from backend.src.core.executor_config.resolver import resolve_executor
from backend.src.core.work_steps import (
    WorkStepDraft,
    create_work_plan,
    transition_request,
    transition_work_step,
)
from backend.src.models import Deliverable, Project, Request, WorkPlan, WorkStep

logger = structlog.get_logger(__name__)

_WORK_STEP_NAME_MAX = 80


def provision_workspace(project: Project) -> Path:
    """Return the on-disk workspace directory for ``project``, creating
    it when missing.

    ``server_managed`` projects get ``<workspace_root>/<project_id>``
    lazily. ``local_import`` / ``github_connected`` projects are
    expected to carry an explicit ``workspace_dir`` already; we still
    fall back to the managed path so a run never dies on a missing
    directory. The caller persists ``project.workspace_dir`` if this
    function had to assign one.
    """
    if project.workspace_dir:
        path = Path(project.workspace_dir)
    else:
        path = Path(app_settings.workspace_root).resolve() / str(project.id)
    path.mkdir(parents=True, exist_ok=True)
    return path


async def plan_and_dispatch_request(
    *,
    request_id: uuid.UUID,
    tenant_id: uuid.UUID,
    session: AsyncSession,
    stream_manager: object,
    executor: object | None = None,
    executor_kind: str | None = None,
    model: str | None = None,
) -> None:
    """Drive a freshly-created Request into execution.

    Idempotent: ``create_work_plan`` flips the Request ``open →
    running``, so a re-delivered queue message finds the Request no
    longer ``open`` and is a safe no-op. Cross-tenant ``request_id``
    raises ``LookupError`` so the worker can ack-then-skip.

    ``executor`` / ``executor_kind`` / ``model`` are passthroughs to
    ``dispatch_run_attempt`` — production leaves them ``None`` so the
    per-tenant ``ExecutorConfig`` is resolved; tests inject a stub
    executor to exercise the orchestration without a live LLM.

    G10 inserts a single decomposer LLM call between the Request
    arriving and the WorkPlan being built. Same model/auth/tool surface
    as the worker LLM, no tools, asks the model to chain-of-thought
    over the ProjectContext and emit a JSON array of WorkStepDrafts.
    When the decomposer or executor is unavailable, falls back to the
    G9 single-step plan.
    """
    stmt = select(Request).where(Request.id == request_id, Request.tenant_id == tenant_id)
    request = (await session.execute(stmt)).scalar_one_or_none()
    if request is None:
        raise LookupError(f"Request {request_id} not in tenant scope {tenant_id}")

    if request.status != RequestStatus.open:
        logger.info(
            "request_worker_skip_non_open",
            request_id=str(request_id),
            status=request.status.value,
        )
        return

    project = await session.get(Project, request.project_id)
    if project is None:
        raise LookupError(f"Project {request.project_id} not found")

    workspace_dir = provision_workspace(project)
    if not project.workspace_dir:
        project.workspace_dir = str(workspace_dir)
        await session.flush()

    intent = (request.intent or "").strip() or f"Request {request.id}"
    steps, created_by, resolved_executor, resolved_kind, resolved_model = await _build_work_steps(
        request=request,
        tenant_id=tenant_id,
        session=session,
        executor=executor,
        executor_kind=executor_kind,
        model=model,
        intent=intent,
    )
    # Hand the resolved executor down so dispatch_run_attempt doesn't
    # re-resolve per WorkStep (saves redundant DB hits + encryption work).
    executor = resolved_executor
    executor_kind = resolved_kind
    model = resolved_model

    plan = await create_work_plan(
        request=request,
        steps=steps,
        created_by=created_by,
        session=session,
    )

    work_steps = (await session.execute(select(WorkStep).where(WorkStep.plan_id == plan.id))).scalars().all()
    total_steps = len(work_steps)
    prior_step_names: list[str] = []
    for step_index, work_step in enumerate(work_steps):
        # ``dispatch_run_attempt`` never raises — failures are encoded
        # as ``RunAttemptStatus.failed`` + a terminal_reason string.
        # It also enqueues the resulting Deliverable on ``proof:queue``
        # so the VerifierWorker picks the loop up from here.
        result = await dispatch_run_attempt(
            request=request,
            work_step=work_step,
            tenant_id=tenant_id,
            session=session,
            step_index=step_index,
            total_steps=total_steps,
            prior_step_names=tuple(prior_step_names),
            stream_manager=stream_manager,
            workspace_dir=workspace_dir,
            executor=executor,  # type: ignore[arg-type]
            executor_kind=executor_kind,
            model=model,
        )
        logger.info(
            "request_worker_dispatched",
            request_id=str(request_id),
            work_step_id=str(work_step.id),
            terminal_reason=result.terminal_reason,
            deliverable_id=str(result.deliverable.id) if result.deliverable else None,
        )
        await session.commit()
        prior_step_names.append(work_step.name)

    # If every WorkStep failed before producing a Deliverable, no proof
    # message was enqueued — advance the Request to ``blocked`` here so
    # it doesn't sit at ``running`` forever with nothing in flight.
    await _maybe_finalize_request(request=request, session=session, stream_manager=stream_manager)


async def advance_request_after_proof(
    *,
    deliverable: Deliverable,
    session: AsyncSession,
    stream_manager: object,
) -> None:
    """Walk the WorkStep + Request state machines forward after one
    Deliverable's proof has resolved.

    Called by the VerifierWorker at the tail of ``process_one``. Soft
    by contract — the caller wraps this in try/except so an
    orchestration hiccup never reverts a verified proof.

    Transitions:
      - the Deliverable's WorkStep ``verifying`` → ``review_ready``
        (verified) or ``failed`` (verification_failed /
        human_review_required).
      - then ``_maybe_finalize_request`` checks whether every WorkStep
        on the Request is terminal and advances the Request to
        ``shipped`` (all review_ready) or ``blocked`` (any failed).
    """
    if deliverable.work_step_id is None:
        return
    work_step = await session.get(WorkStep, deliverable.work_step_id)
    if work_step is None:
        return

    if work_step.status == WorkStepStatus.verifying:
        if deliverable.proof_state == ProofState.verified:
            await transition_work_step(step=work_step, target=WorkStepStatus.review_ready, session=session)
        elif deliverable.proof_state in (
            ProofState.verification_failed,
            ProofState.human_review_required,
        ):
            await transition_work_step(step=work_step, target=WorkStepStatus.failed, session=session)

    request = await session.get(Request, work_step.request_id)
    if request is not None:
        await _maybe_finalize_request(request=request, session=session, stream_manager=stream_manager)


async def _maybe_finalize_request(
    *,
    request: Request,
    session: AsyncSession,
    stream_manager: object,
) -> None:
    """Advance the Request once all its (active-plan) WorkSteps are
    terminal. No-op while any step is still pending/running/verifying.

    All steps ``review_ready`` → ``running → review_ready → shipped``.
    The ``shipped`` transition fires the G8.3 PR hook inside
    ``transition_request``. Any step ``failed`` → ``running →
    blocked``.
    """
    if request.status != RequestStatus.running:
        return

    active_plan = (
        await session.execute(
            select(WorkPlan).where(WorkPlan.request_id == request.id).order_by(WorkPlan.version.desc()).limit(1)
        )
    ).scalar_one_or_none()
    if active_plan is None:
        return

    steps = (await session.execute(select(WorkStep).where(WorkStep.plan_id == active_plan.id))).scalars().all()
    if not steps:
        return

    terminal = {
        WorkStepStatus.review_ready,
        WorkStepStatus.failed,
        WorkStepStatus.skipped,
    }
    if any(step.status not in terminal for step in steps):
        return  # still work in flight

    any_failed = any(step.status == WorkStepStatus.failed for step in steps)
    all_ready = all(step.status == WorkStepStatus.review_ready for step in steps)

    if any_failed:
        await transition_request(request=request, target=RequestStatus.blocked, session=session)
        logger.info("request_finalized_blocked", request_id=str(request.id))
        return

    if all_ready:
        await transition_request(request=request, target=RequestStatus.review_ready, session=session)
        # ``shipped`` is gated on every Deliverable being verified
        # (``_request_has_verified_deliverable_proof``) and fires the
        # G8.3 PR hook. ``transition_request`` raises GreenfieldStateError
        # if the gate fails — let the caller's soft-fail wrapper log it.
        await transition_request(
            request=request,
            target=RequestStatus.shipped,
            session=session,
            github_client_factory=None,
        )
        logger.info("request_finalized_shipped", request_id=str(request.id))


async def _build_work_steps(
    *,
    request: Request,
    tenant_id: uuid.UUID,
    session: AsyncSession,
    executor: ExecutorClient | None,
    executor_kind: str | None,
    model: str | None,
    intent: str,
) -> tuple[list[WorkStepDraft], WorkPlanCreatedBy, ExecutorClient | None, str | None, str | None]:
    """Resolve the WorkPlan's step list for ``request``.

    Tries the CoT decomposer first when an executor is available. Falls
    back to the G9 single-step plan when:
      - no per-tenant ExecutorConfig exists (decomposer would not have
        a model to call anyway — leave that for dispatch_run_attempt to
        record as ``executor_unconfigured``);
      - the decomposer returns no usable steps (handled inside
        ``decompose_request`` — it already emits a single-step fallback,
        so this path is just structural defense).

    Returns ``(steps, created_by, executor, executor_kind, model)`` so
    the caller can reuse the resolved executor for ``dispatch_run_attempt``.
    """
    if executor is None:
        kind, resolved_model = await _lookup_executor_config_kind_and_model(tenant_id=tenant_id, session=session)
        if kind is not None:
            resolved = await resolve_executor(tenant_id=tenant_id, session=session)
            if resolved is not None:
                executor = resolved
                executor_kind = kind
                model = resolved_model

    if executor is None:
        # No executor configured for this tenant. Build a single-step
        # plan so dispatch_run_attempt can attribute the failure as
        # ``executor_unconfigured``.
        step_name = intent.splitlines()[0][:_WORK_STEP_NAME_MAX]
        return (
            [WorkStepDraft(name=step_name, objective=intent, expected_outputs=[])],
            WorkPlanCreatedBy.system,
            executor,
            executor_kind,
            model,
        )

    ctx = await build_project_context(request=request, session=session)
    # Wire-required metadata. The decomposer fires BEFORE any
    # RunAttempt is created, so ``run_id`` is a synthetic per-Request
    # placeholder. BSGateway accepts it; DirectLLMAdapter just needs
    # both fields truthy.
    decomposer_metadata = {
        "tenant_id": str(tenant_id),
        "run_id": f"decompose:{request.id}",
        "request_id": str(request.id),
        "project_id": str(request.project_id),
    }
    steps = await decompose_request(
        ctx,
        executor=executor,
        model=model or "",
        metadata=decomposer_metadata,
    )
    return steps, WorkPlanCreatedBy.llm_assisted, executor, executor_kind, model
