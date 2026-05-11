"""G6.3 — ``dispatch_run_attempt`` drives a Request's first WorkStep
through the RunAttempt phase machine, persists a Deliverable from the
executor output, and enqueues ``proof:queue`` for the VerifierWorker.

These tests stub the :class:`ExecutorClient` Protocol with a tiny
recording adapter so the dispatcher's branching is exercised without
any real LLM or ``ExecutorConfig`` decryption — that surface is
already covered by G6.2 tests. Production callers in G6.4 will pair
this function with ``resolve_executor`` so the per-tenant config row
selects which path executes.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from backend.src.core.deliverables import create_deliverable_from_work_output  # noqa: F401 — surface check
from backend.src.core.domain import (
    DeliverableStatus,
    ProofState,
    RequestStatus,
    RunAttemptPhase,
    RunAttemptStatus,
    WorkPlanCreatedBy,
    WorkStepStatus,
)
from backend.src.core.run_attempt_executor import (
    DispatchRunAttemptResult,
    dispatch_run_attempt,
)
from backend.src.core.work_steps import WorkStepDraft, create_work_plan
from backend.src.models import Deliverable, Project, Request, WorkStep
from backend.src.workers.verifier import PROOF_QUEUE_STREAM


@dataclass
class _RecordingExecutor:
    """Stub ExecutorClient — records each ``execute()`` call and
    returns a canned BSGateway-shape result so the dispatcher's
    persistence path runs end-to-end without a real LLM."""

    response_text: str = "Implemented /healthz endpoint per objective."
    finish_reason: str | None = "stop"
    actual_cost_cents: int = 17
    raise_exc: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def execute(
        self,
        *,
        messages: list[dict[str, Any]],
        metadata: dict[str, Any],
        model: str,
        workspace_dir: str | None = None,
        mcp_servers: dict[str, Any] | None = None,
        on_chunk: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "messages": messages,
                "metadata": metadata,
                "model": model,
                "workspace_dir": workspace_dir,
                "mcp_servers": mcp_servers,
            }
        )
        if self.raise_exc is not None:
            raise self.raise_exc
        if on_chunk is not None and self.response_text:
            await on_chunk(self.response_text)
        return {
            "output_type": "text",
            "output_ref": self.response_text,
            "actual_cost_cents": self.actual_cost_cents,
            "finish_reason": self.finish_reason,
        }


async def _make_request_with_step(db_session, tenant_id: uuid.UUID) -> tuple[Request, WorkStep]:
    project = Project(tenant_id=tenant_id, name="G6.3 Dispatcher", description="")
    db_session.add(project)
    await db_session.flush()
    request = Request(
        tenant_id=tenant_id,
        project_id=project.id,
        intent="Add a /healthz endpoint to the FastAPI app",
    )
    db_session.add(request)
    await db_session.commit()
    await db_session.refresh(request)

    plan = await create_work_plan(
        request=request,
        steps=[
            WorkStepDraft(
                name="Implement /healthz",
                objective="Add a /healthz endpoint that returns 200 OK",
                expected_outputs=["backend/src/api/health.py"],
            ),
        ],
        created_by=WorkPlanCreatedBy.system,
        session=db_session,
    )
    step = (await db_session.execute(select(WorkStep).where(WorkStep.plan_id == plan.id))).scalar_one()
    return request, step


@pytest.mark.asyncio
async def test_dispatch_happy_path_completes_attempt_creates_deliverable_and_enqueues_proof(
    db_session, mock_tenant_id, seeded_tenant, mock_stream_manager
):
    request, step = await _make_request_with_step(db_session, mock_tenant_id)
    executor = _RecordingExecutor()

    result = await dispatch_run_attempt(
        request=request,
        work_step=step,
        tenant_id=mock_tenant_id,
        session=db_session,
        stream_manager=mock_stream_manager,
        executor=executor,
        executor_kind="llm_api",
        model="ollama_chat/qwen3-coder:30b",
    )

    assert isinstance(result, DispatchRunAttemptResult)
    assert result.terminal_reason == "summarized"
    assert result.attempt.phase == RunAttemptPhase.terminal
    assert result.attempt.status == RunAttemptStatus.completed
    assert result.attempt.executor_kind == "llm_api"
    assert result.attempt.model == "ollama_chat/qwen3-coder:30b"

    # Exactly one executor call (single-round work phase in G6.3).
    assert len(executor.calls) == 1

    # Deliverable persisted in draft / verification_missing — the worker
    # flips proof_state asynchronously off ``proof:queue``.
    assert result.deliverable is not None
    persisted = await db_session.get(Deliverable, result.deliverable.id)
    assert persisted is not None
    assert persisted.request_id == request.id
    assert persisted.work_step_id == step.id
    assert persisted.project_id == request.project_id
    assert persisted.status == DeliverableStatus.draft
    assert persisted.proof_state == ProofState.verification_missing
    assert persisted.summary is not None and "/healthz" in persisted.summary

    # WorkStep advanced pending → running → verifying (VerifierWorker
    # owns the verifying → review_ready / failed transition).
    await db_session.refresh(step)
    assert step.status == WorkStepStatus.verifying
    assert step.attempt_count == 1

    # Request stays running until the proof completes.
    await db_session.refresh(request)
    assert request.status == RequestStatus.running

    # ``proof:queue`` was published exactly once with the new deliverable.
    publish_call = next(
        call for call in mock_stream_manager.publish.await_args_list if call.args and call.args[0] == PROOF_QUEUE_STREAM
    )
    payload = publish_call.args[1]
    assert payload["deliverable_id"] == str(result.deliverable.id)
    assert payload["tenant_id"] == str(mock_tenant_id)


@pytest.mark.asyncio
async def test_dispatch_passes_required_audit_metadata_to_executor(
    db_session, mock_tenant_id, seeded_tenant, mock_stream_manager
):
    """``bsvibe_llm`` rejects metadata missing ``tenant_id`` /
    ``run_id``. The dispatcher must hand both to every ExecutorClient
    so the direct path doesn't blow up at the audit hop.
    """
    request, step = await _make_request_with_step(db_session, mock_tenant_id)
    executor = _RecordingExecutor()

    result = await dispatch_run_attempt(
        request=request,
        work_step=step,
        tenant_id=mock_tenant_id,
        session=db_session,
        stream_manager=mock_stream_manager,
        executor=executor,
        executor_kind="llm_api",
        model="ollama_chat/qwen3-coder:30b",
    )

    assert len(executor.calls) == 1
    metadata = executor.calls[0]["metadata"]
    assert metadata["tenant_id"] == str(mock_tenant_id)
    assert metadata["run_id"] == str(result.attempt.id)
    assert metadata["request_id"] == str(request.id)
    assert metadata["project_id"] == str(request.project_id)


@pytest.mark.asyncio
async def test_dispatch_without_executor_or_config_fails_attempt_and_skips_proof_queue(
    db_session, mock_tenant_id, seeded_tenant, mock_stream_manager
):
    request, step = await _make_request_with_step(db_session, mock_tenant_id)

    result = await dispatch_run_attempt(
        request=request,
        work_step=step,
        tenant_id=mock_tenant_id,
        session=db_session,
        stream_manager=mock_stream_manager,
        executor=None,
    )

    assert result.terminal_reason == "executor_unconfigured"
    assert result.deliverable is None
    assert result.attempt.status == RunAttemptStatus.failed
    assert result.attempt.terminal_reason == "executor_unconfigured"
    assert result.attempt.phase == RunAttemptPhase.terminal

    await db_session.refresh(step)
    assert step.status == WorkStepStatus.failed
    # No proof:queue publish when there's no deliverable.
    proof_calls = [
        call for call in mock_stream_manager.publish.await_args_list if call.args and call.args[0] == PROOF_QUEUE_STREAM
    ]
    assert proof_calls == []


@pytest.mark.asyncio
async def test_dispatch_executor_error_fails_attempt_and_skips_deliverable(
    db_session, mock_tenant_id, seeded_tenant, mock_stream_manager
):
    request, step = await _make_request_with_step(db_session, mock_tenant_id)
    executor = _RecordingExecutor(raise_exc=RuntimeError("upstream 503"))

    result = await dispatch_run_attempt(
        request=request,
        work_step=step,
        tenant_id=mock_tenant_id,
        session=db_session,
        stream_manager=mock_stream_manager,
        executor=executor,
        executor_kind="bsgateway",
        model="claude-3-5-sonnet-latest",
    )

    assert result.deliverable is None
    assert result.attempt.status == RunAttemptStatus.failed
    assert "executor_error" in result.attempt.terminal_reason
    assert result.terminal_reason.startswith("executor_error:")

    await db_session.refresh(step)
    assert step.status == WorkStepStatus.failed

    proof_calls = [
        call for call in mock_stream_manager.publish.await_args_list if call.args and call.args[0] == PROOF_QUEUE_STREAM
    ]
    assert proof_calls == []


@pytest.mark.asyncio
async def test_dispatch_resolves_executor_from_per_tenant_config_when_not_injected(
    db_session, mock_tenant_id, seeded_tenant, mock_stream_manager, monkeypatch
):
    """When no executor is injected, the dispatcher uses
    ``resolve_executor`` against the per-tenant ``ExecutorConfig`` row.

    This test patches the resolver so the executor-config /
    encryption path stays scoped to G6.2 tests but the dispatch
    fallback wiring is still locked.
    """
    request, step = await _make_request_with_step(db_session, mock_tenant_id)
    executor = _RecordingExecutor()
    resolved_async_mock = AsyncMock(return_value=executor)
    monkeypatch.setattr(
        "backend.src.core.run_attempt_executor.resolve_executor",
        resolved_async_mock,
    )
    # When the dispatcher falls back to the resolver it also needs
    # kind/model. Patch the config-lookup helper to feed those.
    monkeypatch.setattr(
        "backend.src.core.run_attempt_executor._lookup_executor_config_kind_and_model",
        AsyncMock(return_value=("llm_api", "ollama_chat/qwen3-coder:30b")),
    )

    result = await dispatch_run_attempt(
        request=request,
        work_step=step,
        tenant_id=mock_tenant_id,
        session=db_session,
        stream_manager=mock_stream_manager,
    )

    assert result.terminal_reason == "summarized"
    assert result.deliverable is not None
    assert result.attempt.executor_kind == "llm_api"
    assert result.attempt.model == "ollama_chat/qwen3-coder:30b"
    resolved_async_mock.assert_awaited_once()
