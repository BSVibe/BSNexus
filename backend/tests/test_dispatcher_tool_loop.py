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
from backend.src.core.run_attempt_executor import _workspace_overview, dispatch_run_attempt
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


@dataclass
class _SequenceExecutor:
    """Executor stub that returns raw response dicts in order."""

    responses: list[dict[str, Any]]
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
    ) -> dict[str, Any]:
        self.captured_messages.append([dict(m) for m in messages])
        assert self.responses, "executor called more times than scripted"
        return self.responses.pop(0)


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


def test_workspace_overview_includes_small_seed_file_previews(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "api.py").write_text("def healthz():\n    return {'status': 'broken'}\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_api.py").write_text("from src.api import healthz\n")

    overview = _workspace_overview(tmp_path)

    assert "Tree:" in overview
    assert "src/api.py" in overview
    assert "Seed file previews:" in overview
    assert "return {'status': 'broken'}" in overview


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
    # The file_write must surface on the deliverable's artifact_refs —
    # the verifier reads them as ``changed_files`` and the G8.2 commit
    # step needs them to know what to land on the branch.
    assert persisted.artifact_refs == ["src/helper.py"]

    # Phase machine reached terminal cleanly.
    assert result.attempt.phase == RunAttemptPhase.terminal
    assert result.attempt.status == RunAttemptStatus.completed
    assert result.terminal_reason == "summarized"


@pytest.mark.asyncio
async def test_dispatcher_aggregates_all_writes_into_artifact_refs_deduped(
    db_session, mock_tenant_id, seeded_tenant, mock_stream_manager, tmp_path
):
    """Across multiple rounds the deliverable's ``artifact_refs`` must
    list every file_write target, in first-seen order, with no
    duplicates — a file rewritten in a later round appears once."""
    request, step = await _seed_request_with_step(db_session, mock_tenant_id)
    executor = _ScriptedExecutor(
        tool_call_scripts=[
            [
                {
                    "id": "c1",
                    "name": "file_write",
                    "arguments": {"path": "src/a.py", "content": "A = 1\n"},
                }
            ],
            [
                {
                    "id": "c2",
                    "name": "file_write",
                    "arguments": {"path": "src/b.py", "content": "B = 2\n"},
                },
                {
                    "id": "c3",
                    "name": "file_write",
                    "arguments": {"path": "src/a.py", "content": "A = 11\n"},
                },
            ],
        ],
        final_text="Wrote two files.",
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

    assert result.deliverable is not None
    persisted = await db_session.get(Deliverable, result.deliverable.id)
    assert persisted is not None
    assert persisted.artifact_refs == ["src/a.py", "src/b.py"]


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

    # Tool errors are fed back to the LLM, but a workspace run still
    # cannot produce a deliverable unless a later turn writes a file.
    assert result.terminal_reason == "failed_nonconvergent:no_workspace_write"
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
    assert result.deliverable is None


@pytest.mark.asyncio
async def test_dispatcher_nudges_when_model_returns_plain_text_before_file_write(
    db_session, mock_tenant_id, seeded_tenant, mock_stream_manager, tmp_path
):
    """A workspace run cannot accept prose as work before a file is
    written. The dispatcher gives local LLMs a recovery turn, then only
    summarizes after a successful file_write.
    """
    request, step = await _seed_request_with_step(db_session, mock_tenant_id)
    executor = _SequenceExecutor(
        responses=[
            {
                "output_type": "text",
                "output_ref": "I will update the file.",
                "actual_cost_cents": 0,
                "finish_reason": "stop",
                "tool_calls": None,
            },
            {
                "output_type": "text",
                "output_ref": "",
                "actual_cost_cents": 0,
                "finish_reason": "tool_calls",
                "tool_calls": [
                    {
                        "id": "call_write",
                        "name": "file_write",
                        "arguments": {"path": "src/done.py", "content": "DONE = True\n"},
                    }
                ],
            },
            {
                "output_type": "text",
                "output_ref": "Wrote the file.",
                "actual_cost_cents": 0,
                "finish_reason": "stop",
                "tool_calls": None,
            },
        ]
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

    assert (tmp_path / "src" / "done.py").read_text() == "DONE = True\n"
    assert result.deliverable is not None
    assert result.terminal_reason == "summarized"
    assert any("not created or modified any file" in str(m.get("content")) for m in executor.captured_messages[1])


@pytest.mark.asyncio
async def test_dispatcher_fails_without_deliverable_when_model_never_writes_workspace(
    db_session, mock_tenant_id, seeded_tenant, mock_stream_manager, tmp_path
):
    request, step = await _seed_request_with_step(db_session, mock_tenant_id)
    executor = _ScriptedExecutor(final_text="Done in prose only.")

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
    assert result.terminal_reason == "failed_nonconvergent:no_workspace_write"
    assert result.attempt.status == RunAttemptStatus.failed
    await db_session.refresh(step)
    assert step.status == WorkStepStatus.failed
    proof_calls = [
        call for call in mock_stream_manager.publish.await_args_list if call.args and call.args[0] == "proof:queue"
    ]
    assert proof_calls == []


@pytest.mark.asyncio
async def test_dispatcher_terminates_on_repeated_identical_tool_calls(
    db_session, mock_tenant_id, seeded_tenant, mock_stream_manager, tmp_path
):
    """Repeating the same (tool, args) 4x in a 4-round window must
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
        tool_call_scripts=[repeated_call, repeated_call, repeated_call, repeated_call],
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
async def test_dispatcher_sends_repetition_nudge_back_to_model(
    db_session, mock_tenant_id, seeded_tenant, mock_stream_manager, tmp_path
):
    repeated_read = [
        {
            "id": "call_read",
            "name": "file_read",
            "arguments": {"path": "pyproject.toml"},
        }
    ]
    write_call = [
        {
            "id": "call_write",
            "name": "file_write",
            "arguments": {"path": "src/recovered.py", "content": "VALUE = 'ok'\n"},
        }
    ]
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n')
    request, step = await _seed_request_with_step(db_session, mock_tenant_id)
    executor = _ScriptedExecutor(
        tool_call_scripts=[repeated_read, repeated_read, write_call],
        final_text="Recovered after repetition nudge.",
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

    assert result.deliverable is not None
    assert result.terminal_reason == "summarized"
    assert (tmp_path / "src" / "recovered.py").exists()
    assert any(
        "Do not repeat the same read/list call" in str(message.get("content"))
        for captured in executor.captured_messages
        for message in captured
    )


@pytest.mark.asyncio
async def test_dispatcher_preserves_partial_work_when_budget_hits_after_writes(
    db_session, mock_tenant_id, seeded_tenant, mock_stream_manager, tmp_path
):
    """When the work-phase round budget is exhausted but the model
    *did* produce file writes (real scaffold attempt that doesn't
    converge to a final summary in time), the partial work must
    surface as a Deliverable with ``proof_state=human_review_required``
    and the written paths as ``artifact_refs``. Losing the disk-of-files
    on every budget hit is unacceptable for non-trivial tasks."""
    from backend.src.core.domain import ProofState
    from backend.src.models import RunAttempt, ToolEvent

    # 28 distinct file_writes — enough to exceed work budget (24) by
    # several rounds, with real artifacts to preserve.
    scripts: list[list[dict[str, Any]]] = []
    for i in range(28):
        scripts.append(
            [
                {
                    "id": f"call_{i}",
                    "name": "file_write",
                    "arguments": {"path": f"src/file_{i}.py", "content": f"X = {i}\n"},
                }
            ]
        )
    request, step = await _seed_request_with_step(db_session, mock_tenant_id)
    executor = _ScriptedExecutor(tool_call_scripts=scripts, final_text="never reached")

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

    # Budget hit — terminal reason is a budget-class one.
    assert (
        "phase_round_budget_exceeded:work" in (result.terminal_reason or "")
        or "catastrophic_round_budget_exceeded" in (result.terminal_reason or "")
        or result.terminal_reason == "work_loop_iteration_cap"
    )

    # Deliverable IS created with the written paths and marked for review.
    assert result.deliverable is not None
    persisted = await db_session.get(Deliverable, result.deliverable.id)
    assert persisted is not None
    assert persisted.proof_state == ProofState.human_review_required
    assert len(persisted.artifact_refs) > 0
    # First-seen-order, deduplicated.
    assert persisted.artifact_refs == sorted(set(persisted.artifact_refs), key=persisted.artifact_refs.index)
    # And every artifact is one of the file_write targets the model attempted.
    for ref in persisted.artifact_refs:
        assert ref.startswith("src/file_") and ref.endswith(".py")

    # No proof:queue message — we don't want the verifier to overwrite
    # the human_review_required state we just stamped.
    assert mock_stream_manager.publish.await_count == 0  # type: ignore[attr-defined]

    # Run attempt + work step are still marked failed (no convergence).
    await db_session.refresh(step)
    assert step.status == WorkStepStatus.failed
    attempt = await db_session.get(RunAttempt, result.attempt.id)
    assert attempt is not None
    # Budget / outer-cap terminators set ``failed`` (event-budget) or
    # ``timed_out`` (outer iteration cap); both mean "didn't converge".
    assert attempt.status in {RunAttemptStatus.failed, RunAttemptStatus.timed_out}

    # ToolEvent rows exist for every file_write that did land.
    events = (
        (await db_session.execute(select(ToolEvent).where(ToolEvent.run_attempt_id == result.attempt.id)))
        .scalars()
        .all()
    )
    assert len(events) >= 24  # ran out by budget around the 25th


@pytest.mark.asyncio
async def test_dispatcher_real_task_budget_accommodates_scaffold_workflows(
    db_session, mock_tenant_id, seeded_tenant, mock_stream_manager, tmp_path
):
    """A realistic small scaffold (~12 tool calls: README + config +
    code + tests + a couple shell_exec runs) must converge to
    ``summarized`` without the budget terminating early. Pins the
    minimum-task-size envelope so a future ``PHASE_ROUND_BUDGETS[work]``
    tweak that drops below this floor breaks the test."""
    scripts: list[list[dict[str, Any]]] = [
        [{"id": f"c{i}", "name": "file_write", "arguments": {"path": f"f{i}.py", "content": f"V={i}\n"}}]
        for i in range(10)
    ]
    # Two shell_exec rounds (pytest invocations).
    scripts.extend(
        [
            [{"id": "sh1", "name": "shell_exec", "arguments": {"command": "ls"}}],
            [{"id": "sh2", "name": "shell_exec", "arguments": {"command": "ls -la"}}],
        ]
    )
    request, step = await _seed_request_with_step(db_session, mock_tenant_id)
    executor = _ScriptedExecutor(tool_call_scripts=scripts, final_text="Scaffolded.")

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
    assert result.deliverable is not None
    persisted = await db_session.get(Deliverable, result.deliverable.id)
    assert persisted is not None
    assert len(persisted.artifact_refs) == 10


@pytest.mark.asyncio
async def test_dispatcher_caps_outer_loop_iterations(
    db_session, mock_tenant_id, seeded_tenant, mock_stream_manager, tmp_path
):
    """If the model keeps requesting tool calls past the phase round
    budget, the budget guard fires first. Pin the behaviour so a
    future tweak to the cap doesn't silently bypass the per-phase guard."""
    # 30 distinct file_list calls (different paths so the repetition
    # guard doesn't fire) — enough to exhaust the work-phase round
    # budget (24) before hitting the outer iteration cap (32).
    for sub in range(30):
        (tmp_path / f"d{sub}").mkdir()
    scripts: list[list[dict[str, Any]]] = []
    for i in range(30):
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
