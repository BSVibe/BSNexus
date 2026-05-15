from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from backend.src.core.domain import RunAttemptPhase, RunAttemptStatus, WorkPlanCreatedBy
from backend.src.core.run_attempts import (
    CATASTROPHIC_ROUND_CAP,
    PHASE_ROUND_BUDGETS,
    RunAttemptStateError,
    ToolEventInput,
    accept_llm_phase_output,
    advance_phase,
    create_run_attempt,
    finish_run_attempt,
    record_tool_event,
)
from backend.src.core.work_steps import WorkStepDraft, create_work_plan
from backend.src.models import Project, Request, ToolEvent, WorkStep


async def _make_work_step(db_session, tenant_id: uuid.UUID) -> WorkStep:
    project = Project(tenant_id=tenant_id, name="Greenfield G3", description="")
    db_session.add(project)
    await db_session.flush()
    request = Request(
        tenant_id=tenant_id,
        project_id=project.id,
        intent="Exercise phase-enforced RunAttempt",
    )
    db_session.add(request)
    await db_session.commit()
    await db_session.refresh(request)

    plan = await create_work_plan(
        request=request,
        steps=[WorkStepDraft(name="Implement", objective="Add phase gates")],
        created_by=WorkPlanCreatedBy.system,
        session=db_session,
    )
    return (
        await db_session.execute(select(WorkStep).where(WorkStep.plan_id == plan.id))
    ).scalar_one()


@pytest.mark.asyncio
async def test_run_attempt_starts_in_prepare_and_requires_ordered_phase_transitions(
    db_session, mock_tenant_id, seeded_tenant
):
    step = await _make_work_step(db_session, mock_tenant_id)
    attempt = await create_run_attempt(
        work_step=step,
        executor_kind="local_llm",
        model="qwen3-coder:30b",
        session=db_session,
    )

    assert attempt.phase == RunAttemptPhase.prepare
    assert attempt.status == RunAttemptStatus.running
    assert attempt.telemetry["phase_rounds"]["prepare"] == 0

    with pytest.raises(RunAttemptStateError):
        await advance_phase(attempt=attempt, target=RunAttemptPhase.summarize, session=db_session)

    await advance_phase(attempt=attempt, target=RunAttemptPhase.work, session=db_session)
    assert attempt.phase == RunAttemptPhase.work

    await advance_phase(attempt=attempt, target=RunAttemptPhase.verify, session=db_session)
    await advance_phase(attempt=attempt, target=RunAttemptPhase.summarize, session=db_session)
    with pytest.raises(RunAttemptStateError):
        await advance_phase(attempt=attempt, target=RunAttemptPhase.terminal, session=db_session)


@pytest.mark.asyncio
async def test_allowed_tools_are_phase_enforced(db_session, mock_tenant_id, seeded_tenant):
    step = await _make_work_step(db_session, mock_tenant_id)
    attempt = await create_run_attempt(
        work_step=step,
        executor_kind="local_llm",
        model="qwen3-coder:30b",
        session=db_session,
    )

    with pytest.raises(RunAttemptStateError):
        await record_tool_event(
            attempt=attempt,
            event_input=ToolEventInput(tool_name="file_write", args={"path": "app.py", "content": ""}),
            session=db_session,
        )

    result = await record_tool_event(
        attempt=attempt,
        event_input=ToolEventInput(tool_name="file_read", args={"path": "README.md"}),
        session=db_session,
    )
    assert result.terminated is False
    assert attempt.round_count == 1
    assert attempt.telemetry["phase_rounds"]["prepare"] == 1

    await advance_phase(attempt=attempt, target=RunAttemptPhase.work, session=db_session)
    result = await record_tool_event(
        attempt=attempt,
        event_input=ToolEventInput(
            tool_name="file_write",
            args={"path": "app.py", "content": "print('ok')"},
            writes=["app.py"],
        ),
        session=db_session,
    )
    assert result.terminated is False


@pytest.mark.asyncio
async def test_repeated_identical_tool_calls_terminate_early(db_session, mock_tenant_id, seeded_tenant):
    step = await _make_work_step(db_session, mock_tenant_id)
    attempt = await create_run_attempt(
        work_step=step,
        executor_kind="local_llm",
        model="qwen3-coder:30b",
        session=db_session,
    )
    await advance_phase(attempt=attempt, target=RunAttemptPhase.work, session=db_session)

    first = await record_tool_event(
        attempt=attempt,
        event_input=ToolEventInput(tool_name="file_read", args={"path": "same.py"}),
        session=db_session,
    )
    second = await record_tool_event(
        attempt=attempt,
        event_input=ToolEventInput(tool_name="file_read", args={"path": "same.py"}),
        session=db_session,
    )
    third = await record_tool_event(
        attempt=attempt,
        event_input=ToolEventInput(tool_name="file_read", args={"path": "same.py"}),
        session=db_session,
    )
    fourth = await record_tool_event(
        attempt=attempt,
        event_input=ToolEventInput(tool_name="file_read", args={"path": "same.py"}),
        session=db_session,
    )

    assert first.terminated is False
    assert second.nudge == "You already performed this action. Move to summary or stop."
    assert third.terminated is False
    assert fourth.terminated is True
    assert fourth.terminal_reason == "failed_nonconvergent:repeated_tool_call:file_read"
    assert attempt.phase == RunAttemptPhase.terminal
    assert attempt.status == RunAttemptStatus.failed
    assert attempt.terminal_reason == "failed_nonconvergent:repeated_tool_call:file_read"


@pytest.mark.asyncio
async def test_phase_round_budget_prevents_blind_loop(db_session, mock_tenant_id, seeded_tenant):
    step = await _make_work_step(db_session, mock_tenant_id)
    attempt = await create_run_attempt(
        work_step=step,
        executor_kind="local_llm",
        model="qwen3-coder:30b",
        session=db_session,
    )
    await advance_phase(attempt=attempt, target=RunAttemptPhase.work, session=db_session)

    result = None
    for idx in range(PHASE_ROUND_BUDGETS[RunAttemptPhase.work] + 1):
        result = await record_tool_event(
            attempt=attempt,
            event_input=ToolEventInput(tool_name="file_read", args={"path": f"{idx}.py"}),
            session=db_session,
        )

    assert result is not None
    assert result.terminated is True
    assert result.terminal_reason == "phase_round_budget_exceeded:work"
    assert attempt.status == RunAttemptStatus.timed_out
    # Round count must stay within (budget + 1) — anything wider means
    # the budget guard is firing late.
    assert attempt.round_count <= PHASE_ROUND_BUDGETS[RunAttemptPhase.work] + 1


@pytest.mark.asyncio
async def test_catastrophic_round_cap_is_lower_than_legacy_loop(db_session, mock_tenant_id, seeded_tenant):
    step = await _make_work_step(db_session, mock_tenant_id)
    attempt = await create_run_attempt(
        work_step=step,
        executor_kind="local_llm",
        model="qwen3-coder:30b",
        session=db_session,
    )

    result = None
    for idx in range(CATASTROPHIC_ROUND_CAP):
        if idx == PHASE_ROUND_BUDGETS[RunAttemptPhase.prepare]:
            await advance_phase(attempt=attempt, target=RunAttemptPhase.work, session=db_session)
        result = await record_tool_event(
            attempt=attempt,
            event_input=ToolEventInput(tool_name="file_read", args={"path": f"{idx}.py"}),
            session=db_session,
        )
        if result.terminated:
            break

    assert result is not None
    assert result.terminated is True
    assert attempt.round_count <= CATASTROPHIC_ROUND_CAP


@pytest.mark.asyncio
async def test_llm_payload_cannot_mark_system_fields(db_session, mock_tenant_id, seeded_tenant):
    step = await _make_work_step(db_session, mock_tenant_id)
    attempt = await create_run_attempt(
        work_step=step,
        executor_kind="local_llm",
        model="qwen3-coder:30b",
        session=db_session,
    )

    accepted = accept_llm_phase_output(
        attempt=attempt,
        payload={
            "summary": "Changed the files",
            "proof_state": "verified",
            "request_status": "shipped",
            "terminal_reason": "done",
            "status": "completed",
        },
    )

    assert accepted == {"summary": "Changed the files"}
    assert attempt.phase == RunAttemptPhase.prepare
    assert attempt.status == RunAttemptStatus.running
    assert attempt.terminal_reason is None
    assert attempt.telemetry["ignored_llm_system_fields"] == [
        {
            "phase": "prepare",
            "fields": ["proof_state", "request_status", "status", "terminal_reason"],
        }
    ]


@pytest.mark.asyncio
async def test_terminal_reason_is_required_and_tool_events_are_recorded(
    db_session, mock_tenant_id, seeded_tenant
):
    step = await _make_work_step(db_session, mock_tenant_id)
    attempt = await create_run_attempt(
        work_step=step,
        executor_kind="local_llm",
        model="qwen3-coder:30b",
        session=db_session,
    )
    await record_tool_event(
        attempt=attempt,
        event_input=ToolEventInput(
            tool_name="file_read",
            args={"path": "README.md"},
            args_summary="Read README",
            result_summary="README loaded",
        ),
        session=db_session,
    )

    events = (
        await db_session.execute(select(ToolEvent).where(ToolEvent.run_attempt_id == attempt.id))
    ).scalars().all()
    assert len(events) == 1
    assert events[0].round_index == 1

    with pytest.raises(RunAttemptStateError):
        await finish_run_attempt(
            attempt=attempt,
            status=RunAttemptStatus.completed,
            terminal_reason="",
            session=db_session,
        )

    await finish_run_attempt(
        attempt=attempt,
        status=RunAttemptStatus.completed,
        terminal_reason="summarized_without_verifier",
        session=db_session,
    )
    assert attempt.phase == RunAttemptPhase.terminal
    assert attempt.status == RunAttemptStatus.completed
    assert attempt.terminal_reason == "summarized_without_verifier"
