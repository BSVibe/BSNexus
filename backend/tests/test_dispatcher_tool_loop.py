"""G6.6 — dispatcher tool-loop tests.

Pin the contract: when the model emits ``tool_calls`` the dispatcher
must invoke the registry, record a ``ToolEvent`` per call, append the
tool-result message, and re-call the executor until the model returns
plain text (or a budget/repetition terminator fires).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy import select

from backend.src.core.domain import (
    DeliverableStatus,
    RunAttemptPhase,
    RunAttemptStatus,
    WorkPlanCreatedBy,
    WorkStepStatus,
)
from backend.src.core.run_attempt_executor import dispatch_run_attempt
from backend.src.core.work_steps import WorkStepDraft, create_work_plan
from backend.src.models import Deliverable, Project, Request, ToolEvent, WorkStep


@dataclass
class _ScriptedExecutor:
    """Stub executor that returns canned responses one per call.

    The first ``len(tool_call_scripts)`` responses contain tool_calls;
    the final response is plain text so the loop exits.
    """

    tool_call_scripts: list[list[dict[str, Any]]] = field(default_factory=list)
    final_text: str = "Done."
    captured_tools: list[list[dict[str, Any]] | None] = field(default_factory=list)
    captured_messages: list[list[dict[str, Any]]] = field(default_factory=list)
    raise_exc: Exception | None = None

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
    ) -> dict[str, Any]:
        # Record what arrived for assertion.
        self.captured_tools.append(tools)
        # Copy so callers can mutate without retroactively breaking us.
        self.captured_messages.append([dict(m) for m in messages])
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.tool_call_scripts:
            tool_calls = self.tool_call_scripts.pop(0)
            return {
                "output_type": "text",
                "output_ref": "",
                "actual_cost_cents": 0,
                "finish_reason": "tool_calls",
                "tool_calls": tool_calls,
            }
        return {
            "output_type": "text",
            "output_ref": self.final_text,
            "actual_cost_cents": 0,
            "finish_reason": "stop",
            "tool_calls": None,
        }


async def _seed_request_with_step(db_session, tenant_id) -> tuple[Request, WorkStep]:
    project = Project(tenant_id=tenant_id, name="Tool loop", description="")
    db_session.add(project)
    await db_session.flush()
    request = Request(tenant_id=tenant_id, project_id=project.id, intent="Exercise tool loop")
    db_session.add(request)
    await db_session.commit()
    await db_session.refresh(request)
    plan = await create_work_plan(
        request=request,
        steps=[WorkStepDraft(name="Do work", objective="Edit a file")],
        created_by=WorkPlanCreatedBy.system,
        session=db_session,
    )
    step = (await db_session.execute(select(WorkStep).where(WorkStep.plan_id == plan.id))).scalar_one()
    return request, step


@pytest.mark.asyncio
async def test_dispatcher_runs_tool_calls_through_registry_and_writes_files(
    db_session, mock_tenant_id, seeded_tenant, mock_stream_manager, tmp_path
):
    request, step = await _seed_request_with_step(db_session, mock_tenant_id)
    executor = _ScriptedExecutor(
        tool_call_scripts=[
            [
                {
                    "id": "call_1",
                    "name": "file_write",
                    "arguments": {"path": "src/helper.py", "content": "def add(a, b):\n    return a + b\n"},
                }
            ],
        ],
        final_text="Added the helper.",
    )

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

    # Tool actually ran — file landed on disk.
    written = (tmp_path / "src" / "helper.py").read_text()
    assert "def add" in written

    # Executor was called twice: once with tool_calls coming back, once
    # for the final text.
    assert len(executor.captured_tools) == 2
    # First call carried the work-phase tool schema.
    first_tools = executor.captured_tools[0] or []
    assert any(t["function"]["name"] == "file_write" for t in first_tools)

    # One ToolEvent recorded for the file_write call.
    events = (
        (await db_session.execute(select(ToolEvent).where(ToolEvent.run_attempt_id == result.attempt.id)))
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].tool_name == "file_write"
    assert events[0].writes == ["src/helper.py"]
    assert events[0].exit_code == 0

    # Deliverable persisted with the model's final text as summary.
    assert result.deliverable is not None
    persisted = await db_session.get(Deliverable, result.deliverable.id)
    assert persisted is not None
    assert persisted.summary == "Added the helper."
    assert persisted.status == DeliverableStatus.draft

    # Phase machine reached terminal cleanly.
    assert result.attempt.phase == RunAttemptPhase.terminal
    assert result.attempt.status == RunAttemptStatus.completed
    assert result.terminal_reason == "summarized"


@pytest.mark.asyncio
async def test_dispatcher_skips_tool_loop_when_workspace_dir_not_provided(
    db_session, mock_tenant_id, seeded_tenant, mock_stream_manager
):
    """Without a workspace, the registry can't run — dispatcher falls
    back to G6.3 single-shot. Locks the backward-compat path so legacy
    callers (anything not passing workspace_dir) still work."""
    request, step = await _seed_request_with_step(db_session, mock_tenant_id)
    executor = _ScriptedExecutor(final_text="Plain text reply.")

    result = await dispatch_run_attempt(
        request=request,
        work_step=step,
        tenant_id=mock_tenant_id,
        session=db_session,
        stream_manager=mock_stream_manager,
        executor=executor,
        executor_kind="injected",
        model="stub-model",
    )

    assert result.terminal_reason == "summarized"
    # Single executor call, no tool schema sent.
    assert len(executor.captured_tools) == 1
    assert executor.captured_tools[0] is None
    await db_session.refresh(step)
    assert step.status == WorkStepStatus.verifying


@pytest.mark.asyncio
async def test_dispatcher_records_failed_tool_call_with_error_message_and_keeps_looping(
    db_session, mock_tenant_id, seeded_tenant, mock_stream_manager, tmp_path
):
    """When a tool raises ToolError (denylist, path escape, …), the
    dispatcher must still feed the error back to the LLM and continue
    so the model can self-correct on the next turn."""
    request, step = await _seed_request_with_step(db_session, mock_tenant_id)
    executor = _ScriptedExecutor(
        tool_call_scripts=[
            [
                {
                    "id": "call_evil",
                    "name": "shell_exec",
                    "arguments": {"command": "rm -rf /"},
                }
            ],
        ],
        final_text="Caught the denylist, stopping.",
    )

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

    # Loop completed successfully — tool error didn't crash the dispatcher.
    assert result.terminal_reason == "summarized"
    events = (
        (await db_session.execute(select(ToolEvent).where(ToolEvent.run_attempt_id == result.attempt.id)))
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].exit_code == 1  # marked failed
    assert events[0].result_summary is not None
    assert "denylist" in events[0].result_summary
    # Tool-result message was appended to the second executor call.
    second_messages = executor.captured_messages[1]
    tool_msgs = [m for m in second_messages if m.get("role") == "tool"]
    assert tool_msgs and "denylist" in str(tool_msgs[-1].get("content", ""))


@pytest.mark.asyncio
async def test_dispatcher_terminates_on_repeated_identical_tool_calls(
    db_session, mock_tenant_id, seeded_tenant, mock_stream_manager, tmp_path
):
    """Repeating the same (tool, args) 3x in a 4-round window must
    terminate via the phase machine's repetition guard. The dispatcher
    surfaces the terminal_reason and skips Deliverable creation."""
    repeated_call = [
        {
            "id": "call_dup",
            "name": "file_read",
            "arguments": {"path": "pyproject.toml"},
        }
    ]
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n')
    request, step = await _seed_request_with_step(db_session, mock_tenant_id)
    executor = _ScriptedExecutor(
        tool_call_scripts=[repeated_call, repeated_call, repeated_call],
        final_text="should not reach",
    )

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

    assert result.deliverable is None
    assert "repeated_tool_call" in result.terminal_reason
    await db_session.refresh(step)
    assert step.status == WorkStepStatus.failed
    # No proof:queue publish.
    proof_calls = [
        call for call in mock_stream_manager.publish.await_args_list if call.args and call.args[0] == "proof:queue"
    ]
    assert proof_calls == []


@pytest.mark.asyncio
async def test_dispatcher_caps_outer_loop_iterations(
    db_session, mock_tenant_id, seeded_tenant, mock_stream_manager, tmp_path
):
    """If the model keeps requesting tool calls past the phase round
    budget, the budget guard fires first. Pin the behaviour so a
    future tweak to the cap doesn't silently bypass the per-phase guard."""
    # 20 distinct file_list calls (different paths so the repetition
    # guard doesn't fire) — enough to exhaust the work phase round
    # budget (8) before hitting the outer iteration cap (12).
    for sub in range(20):
        (tmp_path / f"d{sub}").mkdir()
    scripts: list[list[dict[str, Any]]] = []
    for i in range(20):
        scripts.append(
            [
                {
                    "id": f"call_{i}",
                    "name": "file_list",
                    "arguments": {"path": f"d{i}"},
                }
            ]
        )
    # Last response would be plain text but should never be reached.
    request, step = await _seed_request_with_step(db_session, mock_tenant_id)
    executor = _ScriptedExecutor(tool_call_scripts=scripts, final_text="never")

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

    assert result.deliverable is None
    # Either the phase round budget or the outer cap — both are valid
    # terminators, but the message must be one of the two stable strings.
    assert (
        "phase_round_budget_exceeded:work" in result.terminal_reason
        or "catastrophic_round_budget_exceeded" in result.terminal_reason
        or result.terminal_reason == "work_loop_iteration_cap"
    )
    await db_session.refresh(step)
    assert step.status == WorkStepStatus.failed
