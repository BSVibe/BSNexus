"""G6.3 / G6.6 — ``dispatch_run_attempt``: drive a Request's first
WorkStep through the RunAttempt phase machine.

G6.3 wired the single-shot path: one ``ExecutorClient.execute()``
call in ``work`` phase → persist a Deliverable → enqueue ``proof:queue``.

G6.6 promotes the ``work`` phase to a tool-call loop:

  1. Build the phase tool schema from :class:`ToolRegistry`.
  2. ``execute(messages, tools=schema)``.
  3. If the model returns ``tool_calls``, run each through the
     registry, record a ``ToolEvent`` via ``record_tool_event``
     (which enforces phase round budgets + repetition termination
     for free), append tool-result messages, loop.
  4. Otherwise (plain text response) — exit the loop, persist the
     last text as the deliverable summary, advance to verify / summarize.

Callers depend only on the :class:`ExecutorClient` Protocol so a third
executor kind stays one-class. A workspace_dir is required for the
tool loop; without one (legacy callers / G6.3 mode) the dispatcher
falls back to the single-shot path.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    ALLOWED_TOOLS_BY_PHASE,
    ToolEventInput,
    accept_llm_phase_output,
    advance_phase,
    create_run_attempt,
    finish_run_attempt,
    record_tool_event,
)
from backend.src.core.tools import ToolError, ToolRegistry
from backend.src.core.work_steps import transition_work_step
from backend.src.models import Deliverable, Request, RunAttempt, WorkStep
from backend.src.models.executor_config import ExecutorConfig
from backend.src.queue.streams import RedisStreamManager
from backend.src.workers.verifier import PROOF_QUEUE_STREAM

logger = structlog.get_logger(__name__)


SUMMARY_PREVIEW_CHARS = 500

# G6.6 — hard cap on outer work-phase iterations. The
# ``record_tool_event`` per-phase budget already protects against
# runaway loops, but we also bound the *number of LLM round-trips* in
# case the model returns zero tool_calls but garbage text repeatedly
# (no ToolEvent rows means the round-budget logic never fires).
MAX_WORK_LOOP_ITERATIONS = 12


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
    workspace_dir: Path | str | None = None,
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

    tool_registry = _build_tool_registry(workspace_dir)

    try:
        output_text = await _run_work_phase(
            attempt=attempt,
            messages=messages,
            metadata=metadata,
            model=model or "",
            executor=executor,
            tool_registry=tool_registry,
            workspace_dir=workspace_dir,
            session=session,
        )
    except _ToolLoopTerminated as terminated:
        await transition_work_step(step=work_step, target=WorkStepStatus.failed, session=session)
        return DispatchRunAttemptResult(
            attempt=terminated.attempt,
            deliverable=None,
            terminal_reason=terminated.reason,
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
                "Use the tools to read/list/write files in the workspace, run "
                "shell commands when needed, and finish by sending a short "
                "plain-text summary (no tool calls) so the verifier can take over."
            ),
        },
        {"role": "user", "content": user_block},
    ]


def _build_tool_registry(workspace_dir: Path | str | None) -> ToolRegistry | None:
    if workspace_dir is None:
        return None
    path = Path(workspace_dir)
    if not path.exists():
        logger.warning("dispatch_run_attempt_workspace_missing", workspace=str(path))
        return None
    return ToolRegistry(workspace_dir=path)


class _ToolLoopTerminated(Exception):
    """Raised inside the work-phase loop when ``record_tool_event``
    fires a terminal reason (budget / repetition). The outer handler
    transitions the work step and returns the dispatcher result."""

    def __init__(self, *, attempt: RunAttempt, reason: str) -> None:
        super().__init__(reason)
        self.attempt = attempt
        self.reason = reason


async def _run_work_phase(
    *,
    attempt: RunAttempt,
    messages: list[dict[str, Any]],
    metadata: dict[str, Any],
    model: str,
    executor: ExecutorClient,
    tool_registry: ToolRegistry | None,
    workspace_dir: Path | str | None,
    session: AsyncSession,
) -> str:
    """Tool-call loop for the work phase. Returns the model's final
    plain-text response so the dispatcher can persist it as the
    deliverable summary.

    The loop is bounded by both the per-phase ``record_tool_event``
    budget (already wired into the phase machine) and
    ``MAX_WORK_LOOP_ITERATIONS`` as an outer safety net.
    """
    work_tools = list(ALLOWED_TOOLS_BY_PHASE[RunAttemptPhase.work])
    tools_schema = tool_registry.schema_for(work_tools) if tool_registry is not None else None
    workspace_dir_str = str(workspace_dir) if workspace_dir is not None else None
    final_text = ""

    for _ in range(MAX_WORK_LOOP_ITERATIONS):
        executor_result = await executor.execute(
            messages=messages,
            metadata=metadata,
            model=model,
            workspace_dir=workspace_dir_str,
            tools=tools_schema,
        )
        final_text = str(executor_result.get("output_ref") or "")
        tool_calls = executor_result.get("tool_calls")

        if not tool_calls or tool_registry is None:
            return final_text

        messages.append(_assistant_tool_call_message(final_text, tool_calls))

        for call in tool_calls:
            tool_name = str(call.get("name") or "")
            call_id = str(call.get("id") or "")
            arguments = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}

            tool_output, exit_code, writes = await _invoke_tool_safely(tool_registry, tool_name, arguments)
            event_input = ToolEventInput(
                tool_name=tool_name,
                args=arguments,
                args_summary=_short_args_summary(tool_name, arguments),
                result_summary=tool_output[:200],
                exit_code=exit_code,
                writes=writes,
            )
            try:
                event_result = await record_tool_event(attempt=attempt, event_input=event_input, session=session)
            except Exception as exc:
                # Tool not allowed in this phase, etc. — surface and stop.
                raise _ToolLoopTerminated(attempt=attempt, reason=f"tool_event_error:{exc.__class__.__name__}") from exc

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": tool_output,
                }
            )
            if event_result.terminated:
                raise _ToolLoopTerminated(
                    attempt=attempt,
                    reason=event_result.terminal_reason or "work_loop_terminated",
                )

    # Outer cap hit without convergence — terminate.
    await finish_run_attempt(
        attempt=attempt,
        status=RunAttemptStatus.timed_out,
        terminal_reason="work_loop_iteration_cap",
        session=session,
    )
    raise _ToolLoopTerminated(attempt=attempt, reason="work_loop_iteration_cap")


async def _invoke_tool_safely(
    registry: ToolRegistry, name: str, arguments: dict[str, Any]
) -> tuple[str, int, list[str]]:
    """Run ``registry.invoke`` and translate failures into a string
    the LLM can read. Returns (output, exit_code, writes)."""
    writes: list[str] = []
    if name == "file_write":
        path = arguments.get("path")
        if isinstance(path, str):
            writes.append(path)
    try:
        output = await registry.invoke(name, arguments)
        return output, 0, writes
    except ToolError as exc:
        return f"ERROR: {exc}", 1, writes


def _assistant_tool_call_message(text: str, tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
    """LiteLLM/OpenAI assistant turn that carries tool_calls. We
    preserve any leading text the model emitted alongside the calls
    (otherwise some providers reject the next turn for ``content``
    being null *and* ``tool_calls`` being present)."""
    return {
        "role": "assistant",
        "content": text or None,
        "tool_calls": [
            {
                "id": str(call.get("id") or ""),
                "type": "function",
                "function": {
                    "name": str(call.get("name") or ""),
                    "arguments": json.dumps(call.get("arguments") or {}),
                },
            }
            for call in tool_calls
        ],
    }


def _short_args_summary(tool_name: str, arguments: dict[str, Any]) -> str:
    if tool_name == "file_write":
        path = arguments.get("path") or "?"
        content = arguments.get("content") or ""
        return f"file_write {path} ({len(content)} chars)"
    if tool_name == "shell_exec":
        return f"shell_exec {str(arguments.get('command') or '')[:120]}"
    return f"{tool_name} {json.dumps(arguments)[:200]}"
