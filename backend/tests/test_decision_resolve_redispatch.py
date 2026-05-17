"""Step 6 — resolving a blocking Decision re-engages the stalled work.

Pins the forward-only contract from
``~/Docs/BSNexus/planning/no-blocked-decision-routing-design.md``:

- ``POST /decisions/{id}/resolve`` flips a ``needs_decision`` Request +
  WorkStep back to ``running`` and ENQUEUES a re-dispatch on
  ``request:queue`` — never runs ``dispatch_run_attempt`` inline.
- ``retry`` re-dispatches the WorkStep as-is.
- ``reframe`` seeds the founder's free-text ``guidance`` into the next
  attempt's messages as added direction.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy import select

from backend.src.core.domain import RequestStatus, WorkPlanCreatedBy, WorkStepStatus
from backend.src.core.orchestration import RE_ENGAGE_KIND, re_dispatch_decision, re_engage_request
from backend.src.core.work_steps import WorkStepDraft, create_work_plan, transition_request
from backend.src.models import Decision, Project, Request, RunAttempt, WorkStep


@dataclass
class _ScriptedExecutor:
    """One canned response per call — scripted tool calls first, then
    plain ``final_text`` ends the loop. Records every message list."""

    tool_call_scripts: list[list[dict[str, Any]]] = field(default_factory=list)
    final_text: str = "Done."
    captured_messages: list[list[dict[str, Any]]] = field(default_factory=list)

    async def execute(
        self,
        *,
        messages: list[dict[str, Any]],
        metadata: dict[str, Any],
        model: str,
        workspace_dir: str | None = None,
        mcp_servers: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        on_chunk: Callable[[str], Awaitable[None]] | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        self.captured_messages.append([dict(m) for m in messages])
        if self.tool_call_scripts:
            return {
                "output_type": "text",
                "output_ref": "",
                "actual_cost_cents": 0,
                "finish_reason": "tool_calls",
                "tool_calls": self.tool_call_scripts.pop(0),
            }
        return {
            "output_type": "text",
            "output_ref": self.final_text,
            "actual_cost_cents": 0,
            "finish_reason": "stop",
            "tool_calls": None,
        }


async def _seed_request_in_needs_decision(
    db_session,
    tenant_id: uuid.UUID,
    *,
    workspace_dir: str,
    step_status: WorkStepStatus = WorkStepStatus.needs_decision,
) -> tuple[Request, WorkStep, Decision]:
    """Seed a Request stalled in ``needs_decision`` with a blocking
    Decision attached to its WorkStep.

    ``step_status`` selects which stuck state the WorkStep sits in:
      - ``needs_decision`` — the executor continuation-cap park.
      - ``failed`` — a verification-failed deliverable backstopped by
        ``_maybe_finalize_request`` (the step stays ``failed``).
    """
    project = Project(tenant_id=tenant_id, name="Redispatch", description="", workspace_dir=workspace_dir)
    db_session.add(project)
    await db_session.flush()
    request = Request(tenant_id=tenant_id, project_id=project.id, intent="Build the thing")
    db_session.add(request)
    await db_session.commit()
    await db_session.refresh(request)
    plan = await create_work_plan(
        request=request,
        steps=[WorkStepDraft(name="Implement", objective="Edit files")],
        created_by=WorkPlanCreatedBy.system,
        session=db_session,
    )
    step = (await db_session.execute(select(WorkStep).where(WorkStep.plan_id == plan.id))).scalar_one()
    # Drive request + step into the stalled state.
    step.status = step_status
    await transition_request(request=request, target=RequestStatus.needs_decision, session=db_session)
    decision = Decision(
        tenant_id=tenant_id,
        project_id=project.id,
        request_id=request.id,
        work_step_id=step.id,
        question="How should the stalled work move forward?",
        options=["retry", "reframe"],
        blocking=True,
    )
    db_session.add(decision)
    await db_session.commit()
    await db_session.refresh(decision)
    await db_session.refresh(request)
    await db_session.refresh(step)
    return request, step, decision


@pytest.mark.asyncio
async def test_resolve_retry_re_engages_request_and_enqueues(
    client, db_session, mock_tenant_id, mock_stream_manager, seeded_tenant, tmp_path
):
    request, step, decision = await _seed_request_in_needs_decision(
        db_session, mock_tenant_id, workspace_dir=str(tmp_path)
    )

    resp = await client.post(
        f"/api/v1/decisions/{decision.id}/resolve",
        json={"resolution": "retry", "resolved_by": "founder@test"},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200, resp.text

    await db_session.refresh(request)
    await db_session.refresh(step)
    # Request + WorkStep re-engaged.
    assert request.status == RequestStatus.running
    assert step.status == WorkStepStatus.running

    # A re-dispatch was ENQUEUED on request:queue — not run inline.
    re_engage_calls = [c for c in mock_stream_manager.publish.call_args_list if c.args and c.args[0] == "request:queue"]
    assert len(re_engage_calls) == 1
    payload = re_engage_calls[0].args[1]
    assert payload["kind"] == RE_ENGAGE_KIND
    assert payload["decision_id"] == str(decision.id)
    assert payload["request_id"] == str(request.id)


@pytest.mark.asyncio
async def test_resolve_reframe_persists_guidance(
    client, db_session, mock_tenant_id, mock_stream_manager, seeded_tenant, tmp_path
):
    _request, _step, decision = await _seed_request_in_needs_decision(
        db_session, mock_tenant_id, workspace_dir=str(tmp_path)
    )

    resp = await client.post(
        f"/api/v1/decisions/{decision.id}/resolve",
        json={
            "resolution": "reframe",
            "resolved_by": "founder@test",
            "guidance": "Use Postgres, not SQLite, for the user store.",
        },
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["resolution"] == "reframe"
    assert body["guidance"] == "Use Postgres, not SQLite, for the user store."

    await db_session.refresh(decision)
    assert decision.guidance == "Use Postgres, not SQLite, for the user store."


@pytest.mark.asyncio
async def test_resolve_rejects_unknown_resolution(client, db_session, mock_tenant_id, seeded_tenant, tmp_path):
    """Forward-only — ``abandon`` (or any value outside retry/reframe)
    is a 422; there is no dead-end option."""
    _request, _step, decision = await _seed_request_in_needs_decision(
        db_session, mock_tenant_id, workspace_dir=str(tmp_path)
    )

    resp = await client.post(
        f"/api/v1/decisions/{decision.id}/resolve",
        json={"resolution": "abandon", "resolved_by": "founder@test"},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_re_dispatch_reframe_guidance_reaches_next_attempt(
    db_session, mock_tenant_id, seeded_tenant, mock_stream_manager, tmp_path
):
    """``re_dispatch_decision`` for a ``reframe`` Decision seeds the
    founder's guidance into the next RunAttempt's messages."""
    request, step, decision = await _seed_request_in_needs_decision(
        db_session, mock_tenant_id, workspace_dir=str(tmp_path)
    )
    # Resolve as reframe with guidance (mimics the API write).
    decision.resolution = "reframe"
    decision.guidance = "Switch the storage layer to Postgres."
    # Re-engagement flips request + step back to running.
    step.status = WorkStepStatus.running
    await transition_request(request=request, target=RequestStatus.running, session=db_session)
    await db_session.commit()

    executor = _ScriptedExecutor(
        tool_call_scripts=[
            [{"id": "c0", "name": "file_write", "arguments": {"path": "src/a.py", "content": "X = 1\n"}}]
        ],
        final_text="Re-done with the new direction.",
    )

    await re_dispatch_decision(
        decision_id=decision.id,
        tenant_id=mock_tenant_id,
        session=db_session,
        stream_manager=mock_stream_manager,
        executor=executor,
        executor_kind="injected",
        model="stub-model",
    )

    # The founder's guidance reached the first attempt's prompt.
    all_content = [str(m.get("content")) for captured in executor.captured_messages for m in captured]
    assert any("Switch the storage layer to Postgres." in c for c in all_content)
    assert any("CONTINUATION —" in c for c in all_content)


@pytest.mark.asyncio
async def test_re_dispatch_retry_runs_step_without_guidance(
    db_session, mock_tenant_id, seeded_tenant, mock_stream_manager, tmp_path
):
    """A ``retry`` re-dispatch runs the WorkStep with no CONTINUATION /
    guidance block — a clean fresh attempt."""
    request, step, decision = await _seed_request_in_needs_decision(
        db_session, mock_tenant_id, workspace_dir=str(tmp_path)
    )
    decision.resolution = "retry"
    step.status = WorkStepStatus.running
    await transition_request(request=request, target=RequestStatus.running, session=db_session)
    await db_session.commit()

    executor = _ScriptedExecutor(
        tool_call_scripts=[
            [{"id": "c0", "name": "file_write", "arguments": {"path": "src/a.py", "content": "X = 1\n"}}]
        ],
        final_text="Retried.",
    )

    await re_dispatch_decision(
        decision_id=decision.id,
        tenant_id=mock_tenant_id,
        session=db_session,
        stream_manager=mock_stream_manager,
        executor=executor,
        executor_kind="injected",
        model="stub-model",
    )

    all_content = [str(m.get("content")) for captured in executor.captured_messages for m in captured]
    assert not any("CONTINUATION —" in c for c in all_content)


@pytest.mark.asyncio
async def test_retry_round_trip_from_needs_decision_step(
    db_session, mock_tenant_id, seeded_tenant, mock_stream_manager, tmp_path
):
    """Full round-trip: a ``needs_decision``-parked WorkStep → resolve
    with ``retry`` (re-engage) → ``re_dispatch_decision`` re-runs it
    without raising. Request → running, WorkStep → running, fresh
    RunAttempt, no GreenfieldStateError."""
    request, step, decision = await _seed_request_in_needs_decision(
        db_session, mock_tenant_id, workspace_dir=str(tmp_path), step_status=WorkStepStatus.needs_decision
    )

    # Re-engage: flips request + step back to running.
    await re_engage_request(decision=decision, session=db_session, stream_manager=mock_stream_manager)
    await db_session.refresh(request)
    await db_session.refresh(step)
    assert request.status == RequestStatus.running
    assert step.status == WorkStepStatus.running

    decision.resolution = "retry"
    await db_session.commit()

    executor = _ScriptedExecutor(
        tool_call_scripts=[
            [{"id": "c0", "name": "file_write", "arguments": {"path": "src/a.py", "content": "X = 1\n"}}]
        ],
        final_text="Retried.",
    )
    # Must not raise GreenfieldStateError.
    await re_dispatch_decision(
        decision_id=decision.id,
        tenant_id=mock_tenant_id,
        session=db_session,
        stream_manager=mock_stream_manager,
        executor=executor,
        executor_kind="injected",
        model="stub-model",
    )

    runs = (await db_session.execute(select(RunAttempt).where(RunAttempt.work_step_id == step.id))).scalars().all()
    assert len(runs) >= 1


@pytest.mark.asyncio
async def test_retry_round_trip_from_failed_step_does_not_crash(
    client, db_session, mock_tenant_id, seeded_tenant, mock_stream_manager, tmp_path
):
    """Regression for the RequestWorker poison-message loop:
    a ``failed``-backstopped WorkStep (verification-failed deliverable)
    resolved with ``retry`` must re-dispatch cleanly.

    Before the fix ``WORK_STEP_TRANSITIONS[failed]`` was terminal and
    ``re_engage_request`` skipped a non-``needs_decision`` step, so
    ``_execute_one_attempt`` attempted an illegal ``failed → running``
    transition → ``GreenfieldStateError``.
    """
    request, step, decision = await _seed_request_in_needs_decision(
        db_session, mock_tenant_id, workspace_dir=str(tmp_path), step_status=WorkStepStatus.failed
    )

    # Resolve via the founder UI path — payload omits ``resolved_by``.
    resp = await client.post(
        f"/api/v1/decisions/{decision.id}/resolve",
        json={"resolution": "retry"},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200, resp.text

    await db_session.refresh(request)
    await db_session.refresh(step)
    # The ``failed`` step was re-engaged to ``running``.
    assert request.status == RequestStatus.running
    assert step.status == WorkStepStatus.running

    decision = (await db_session.execute(select(Decision).where(Decision.id == decision.id))).scalar_one()
    decision.resolution = "retry"
    await db_session.commit()

    executor = _ScriptedExecutor(
        tool_call_scripts=[
            [{"id": "c0", "name": "file_write", "arguments": {"path": "src/a.py", "content": "X = 1\n"}}]
        ],
        final_text="Retried after verification failure.",
    )
    # The crash being regression-tested: this call previously raised
    # GreenfieldStateError inside ``_execute_one_attempt``.
    await re_dispatch_decision(
        decision_id=decision.id,
        tenant_id=mock_tenant_id,
        session=db_session,
        stream_manager=mock_stream_manager,
        executor=executor,
        executor_kind="injected",
        model="stub-model",
    )

    runs = (await db_session.execute(select(RunAttempt).where(RunAttempt.work_step_id == step.id))).scalars().all()
    assert len(runs) >= 1
    await db_session.refresh(step)
    # The step left ``failed`` — it is no longer a dead-end.
    assert step.status != WorkStepStatus.failed
