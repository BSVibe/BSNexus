"""Tier 1 — budget / handoff / continuation tests.

Pins the contract from
``~/Docs/BSNexus_Budget_Handoff_Continuation_Design_2026-05-16.md``:

- A RunAttempt that exhausts its work-round budget WITH real file
  writes is not a failure — a model-independent handoff record is
  generated and a fresh RunAttempt for the SAME WorkStep resumes it.
- Continuation is bounded by ``MAX_BUDGET_CONTINUATIONS`` (3).
- The continuation is seeded ONLY from the handoff record (no inherited
  LLM message history).
- A 0-write stall does NOT continue — it is a genuine failure.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy import select

from backend.src.core.domain import RunAttemptStatus, WorkPlanCreatedBy, WorkStepStatus
from backend.src.core.run_attempt_executor import (
    MAX_BUDGET_CONTINUATIONS,
    _build_messages,
    _coerce_handoff,
    _fallback_handoff,
    _is_budget_termination,
    dispatch_run_attempt,
)
from backend.src.core.work_steps import WorkStepDraft, create_work_plan
from backend.src.models import Project, Request, RunAttempt, WorkStep


@dataclass
class _ScriptedExecutor:
    """Stub executor — one canned response per call. Tool-call scripts
    are consumed first; once exhausted, plain ``final_text`` ends the
    loop. Shared across continuation attempts (mirrors a real model)."""

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


async def _seed_request_with_step(db_session, tenant_id) -> tuple[Request, WorkStep]:
    project = Project(tenant_id=tenant_id, name="Tier1", description="")
    db_session.add(project)
    await db_session.flush()
    request = Request(tenant_id=tenant_id, project_id=project.id, intent="Build the thing")
    db_session.add(request)
    await db_session.commit()
    await db_session.refresh(request)
    plan = await create_work_plan(
        request=request,
        steps=[WorkStepDraft(name="Do work", objective="Edit files")],
        created_by=WorkPlanCreatedBy.system,
        session=db_session,
    )
    step = (await db_session.execute(select(WorkStep).where(WorkStep.plan_id == plan.id))).scalar_one()
    return request, step


def _write_scripts(n: int) -> list[list[dict[str, Any]]]:
    return [
        [{"id": f"c{i}", "name": "file_write", "arguments": {"path": f"src/f{i}.py", "content": f"X = {i}\n"}}]
        for i in range(n)
    ]


# ───────────────────────── pure helper tests ─────────────────────────


def test_is_budget_termination_recognizes_budget_reasons():
    assert _is_budget_termination("phase_round_budget_exceeded:work")
    assert _is_budget_termination("work_loop_iteration_cap")
    assert _is_budget_termination("catastrophic_round_budget_exceeded")
    assert not _is_budget_termination("summarized")
    assert not _is_budget_termination("repetition_detected")


def test_fallback_handoff_has_all_fields():
    handoff = _fallback_handoff(["src/a.py", "src/b.py"], "wrote two files")
    assert set(handoff) == {"summary", "files_touched", "verification_state", "remaining", "blockers"}
    assert handoff["files_touched"] == ["src/a.py", "src/b.py"]
    assert "wrote two files" in handoff["summary"]


def test_coerce_handoff_normalizes_partial_llm_output():
    raw = {"summary": "did X", "files_touched": ["a.py", "  ", "b.py"]}
    handoff = _coerce_handoff(raw, ["fallback.py"], "final")
    assert handoff["summary"] == "did X"
    assert handoff["files_touched"] == ["a.py", "b.py"]  # blank dropped
    assert handoff["verification_state"] == "(unknown)"
    assert handoff["blockers"] == ""


def test_coerce_handoff_falls_back_on_non_dict():
    handoff = _coerce_handoff("not a dict", ["w.py"], "final text")
    assert handoff["files_touched"] == ["w.py"]
    assert "final text" in handoff["summary"]


def test_coerce_handoff_uses_written_paths_when_files_missing():
    handoff = _coerce_handoff({"summary": "s"}, ["w1.py", "w2.py"], "f")
    assert handoff["files_touched"] == ["w1.py", "w2.py"]


# ──────────────────── _build_messages handoff seeding ─────────────────


@pytest.mark.asyncio
async def test_build_messages_injects_continuation_section(db_session, mock_tenant_id, seeded_tenant):
    request, step = await _seed_request_with_step(db_session, mock_tenant_id)
    handoff = {
        "summary": "scaffolded the API module",
        "files_touched": ["src/api.py", "src/models.py"],
        "verification_state": "tests written, 2 failing",
        "remaining": "fix the failing tests",
        "blockers": "unclear schema for the user table",
    }
    messages = _build_messages(request=request, work_step=step, handoff=handoff)
    user_content = next(m["content"] for m in messages if m["role"] == "user")
    assert "CONTINUATION —" in user_content
    assert "scaffolded the API module" in user_content
    assert "src/api.py" in user_content
    assert "fix the failing tests" in user_content
    assert "unclear schema for the user table" in user_content


@pytest.mark.asyncio
async def test_build_messages_no_continuation_section_without_handoff(db_session, mock_tenant_id, seeded_tenant):
    request, step = await _seed_request_with_step(db_session, mock_tenant_id)
    messages = _build_messages(request=request, work_step=step)
    user_content = next(m["content"] for m in messages if m["role"] == "user")
    assert "CONTINUATION —" not in user_content


# ─────────────────── continuation behavior (integration) ──────────────


@pytest.mark.asyncio
async def test_budget_exhaustion_with_writes_spawns_continuation(
    db_session, mock_tenant_id, seeded_tenant, mock_stream_manager, tmp_path
):
    """Budget hit + real writes + under cap → handoff generated, a fresh
    RunAttempt resumes and converges. Two RunAttempt rows; the first
    carries the persisted handoff record."""
    # 60 writes: attempt 1 exhausts the 48-round budget; the continuation
    # consumes the remainder then converges on ``final_text``.
    executor = _ScriptedExecutor(tool_call_scripts=_write_scripts(60), final_text="Finished the work.")
    request, step = await _seed_request_with_step(db_session, mock_tenant_id)

    result = await dispatch_run_attempt(
        request=request,
        work_step=step,
        tenant_id=mock_tenant_id,
        session=db_session,
        stream_manager=mock_stream_manager,
        executor=executor,
        executor_kind="injected",
        model="stub-model",
        workspace_dir=tmp_path,
    )

    assert result.terminal_reason == "summarized"

    attempts = (await db_session.execute(select(RunAttempt).where(RunAttempt.work_step_id == step.id))).scalars().all()
    assert len(attempts) == 2, "original RunAttempt + one continuation"
    first, second = sorted(attempts, key=lambda a: a.started_at)
    assert first.handoff is not None
    assert set(first.handoff) == {"summary", "files_touched", "verification_state", "remaining", "blockers"}
    assert second.handoff is None  # the converged continuation needs no handoff

    # The continuation's prompt was seeded with the CONTINUATION section.
    assert any("CONTINUATION —" in str(m.get("content")) for captured in executor.captured_messages for m in captured)


@pytest.mark.asyncio
async def test_zero_write_stall_does_not_continue(
    db_session, mock_tenant_id, seeded_tenant, mock_stream_manager, tmp_path
):
    """Budget hit with ZERO file writes is a genuine failure — no
    handoff, no continuation, exactly one RunAttempt."""
    for i in range(60):
        (tmp_path / f"d{i}").mkdir()
    scripts = [[{"id": f"c{i}", "name": "file_list", "arguments": {"path": f"d{i}"}}] for i in range(60)]
    executor = _ScriptedExecutor(tool_call_scripts=scripts, final_text="never")
    request, step = await _seed_request_with_step(db_session, mock_tenant_id)

    result = await dispatch_run_attempt(
        request=request,
        work_step=step,
        tenant_id=mock_tenant_id,
        session=db_session,
        stream_manager=mock_stream_manager,
        executor=executor,
        executor_kind="injected",
        model="stub-model",
        workspace_dir=tmp_path,
    )

    assert _is_budget_termination(result.terminal_reason or "")
    attempts = (await db_session.execute(select(RunAttempt).where(RunAttempt.work_step_id == step.id))).scalars().all()
    assert len(attempts) == 1, "0-write stall must not spawn a continuation"
    assert attempts[0].handoff is None
    await db_session.refresh(step)
    assert step.status == WorkStepStatus.failed


@pytest.mark.asyncio
async def test_continuation_cap_stops_after_max_attempts(
    db_session, mock_tenant_id, seeded_tenant, mock_stream_manager, tmp_path
):
    """If every attempt exhausts the budget with writes, continuation
    stops at the cap: original + MAX_BUDGET_CONTINUATIONS RunAttempts,
    then the WorkStep fails for human review."""
    # 240 writes — enough to exhaust the budget on all 4 attempts.
    executor = _ScriptedExecutor(tool_call_scripts=_write_scripts(240), final_text="never reached")
    request, step = await _seed_request_with_step(db_session, mock_tenant_id)

    result = await dispatch_run_attempt(
        request=request,
        work_step=step,
        tenant_id=mock_tenant_id,
        session=db_session,
        stream_manager=mock_stream_manager,
        executor=executor,
        executor_kind="injected",
        model="stub-model",
        workspace_dir=tmp_path,
    )

    assert _is_budget_termination(result.terminal_reason or "")
    attempts = (await db_session.execute(select(RunAttempt).where(RunAttempt.work_step_id == step.id))).scalars().all()
    assert len(attempts) == MAX_BUDGET_CONTINUATIONS + 1 == 4
    await db_session.refresh(step)
    assert step.status == WorkStepStatus.failed
    # Every non-final attempt produced a handoff; the capped final one did not.
    by_start = sorted(attempts, key=lambda a: a.started_at)
    assert all(a.handoff is not None for a in by_start[:-1])
    assert by_start[-1].handoff is None
    assert by_start[-1].status in {RunAttemptStatus.failed, RunAttemptStatus.timed_out}


@pytest.mark.asyncio
async def test_soft_pressure_message_injected_before_budget(
    db_session, mock_tenant_id, seeded_tenant, mock_stream_manager, tmp_path
):
    """As the work phase nears its budget, a one-shot 'budget nearly
    exhausted' nudge is injected so the model lands in a clean state."""
    executor = _ScriptedExecutor(tool_call_scripts=_write_scripts(60), final_text="done")
    request, step = await _seed_request_with_step(db_session, mock_tenant_id)

    await dispatch_run_attempt(
        request=request,
        work_step=step,
        tenant_id=mock_tenant_id,
        session=db_session,
        stream_manager=mock_stream_manager,
        executor=executor,
        executor_kind="injected",
        model="stub-model",
        workspace_dir=tmp_path,
    )

    assert any(
        "BUDGET NEARLY EXHAUSTED" in str(m.get("content")) for captured in executor.captured_messages for m in captured
    )


@pytest.mark.asyncio
async def test_aspect_retry_budget_floor_routes_to_continuation(
    db_session, mock_tenant_id, seeded_tenant, mock_stream_manager, tmp_path
):
    """When the work phase converges having burned most of its budget
    and a declared check still fails, the aspect-feedback loop must NOT
    start a doomed retry — it routes through the Tier 1 handoff path
    (``aspect_retry_budget_exhausted``) so a fresh continuation gets a
    full budget."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    # 1 declare_verification (a failing `false` command check) + 40
    # file_writes → the work phase converges at ~41 work rounds, leaving
    # fewer than ASPECT_RETRY_BUDGET_FLOOR rounds in the 48-round budget.
    declare = [
        {
            "id": "d",
            "name": "declare_verification",
            "arguments": {"checks": [{"kind": "command", "command": "false"}]},
        }
    ]
    executor = _ScriptedExecutor(tool_call_scripts=[declare, *_write_scripts(40)], final_text="Converged.")
    request, step = await _seed_request_with_step(db_session, mock_tenant_id)

    await dispatch_run_attempt(
        request=request,
        work_step=step,
        tenant_id=mock_tenant_id,
        session=db_session,
        stream_manager=mock_stream_manager,
        executor=executor,
        executor_kind="injected",
        model="stub-model",
        workspace_dir=tmp_path,
    )

    attempts = (
        (await db_session.execute(select(RunAttempt).where(RunAttempt.work_step_id == step.id))).scalars().all()
    )
    # The budget floor routed to a handoff → a continuation RunAttempt.
    assert len(attempts) >= 2
    first = sorted(attempts, key=lambda a: a.started_at)[0]
    assert first.terminal_reason == "aspect_retry_budget_exhausted"
    assert first.handoff is not None
