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
    ProofState,
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
MAX_WORK_LOOP_ITERATIONS = 32
MAX_NO_WORK_NUDGES = 2


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
    tool_registry = _build_tool_registry(workspace_dir)
    workspace_overview = _workspace_overview(workspace_dir) if tool_registry is not None else None
    messages = _build_messages(
        request=request, work_step=work_step, workspace_overview=workspace_overview
    )

    try:
        output_text, written_paths = await _run_work_phase(
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
        # Partial-work preservation: if the model produced any file
        # writes before termination, surface them as a deliverable
        # so the founder can inspect what landed instead of staring
        # at a null deliverable. Run aspects on what we have to give
        # the founder *diagnostic* signal (which lint errors? which
        # tests fail? does it install?) — but a non-converged run
        # can never be auto-``verified``, so we cap the roll-up at
        # ``human_review_required``.
        # No proof:queue enqueue — the verifier worker must not
        # overwrite the state we just stamped.
        partial_deliverable = None
        if terminated.written_paths:
            partial_deliverable = await create_deliverable_from_work_output(
                tenant_id=tenant_id,
                draft=WorkOutputDraft(
                    project_id=request.project_id,
                    request_id=request.id,
                    work_step_id=work_step.id,
                    title=work_step.name,
                    summary=(terminated.final_text or "")[:SUMMARY_PREVIEW_CHARS] or None,
                    type=DeliverableType.code,
                    artifact_refs=list(terminated.written_paths),
                ),
                session=session,
            )
            await session.flush()
            # Lazy-import: ``orchestration`` → ``run_attempt_executor``
            # → ``verification`` shares the import-cycle break we set
            # up for the back-half orchestration hook.
            from backend.src.core.verification import run_verification  # noqa: PLC0415

            try:
                await run_verification(
                    deliverable=partial_deliverable,
                    workspace_root=workspace_dir or "/tmp",
                    session=session,
                    changed_files=tuple(terminated.written_paths),
                )
            except Exception:
                logger.exception(
                    "partial_deliverable_verification_crashed",
                    deliverable_id=str(partial_deliverable.id),
                )
            # Demote ``verified`` → ``human_review_required``. The
            # aspects ran for diagnostics, but the model didn't reach
            # a final summary; the run is non-converged by definition.
            if partial_deliverable.proof_state == ProofState.verified:
                partial_deliverable.proof_state = ProofState.human_review_required
                await session.flush()
        return DispatchRunAttemptResult(
            attempt=terminated.attempt,
            deliverable=partial_deliverable,
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
            artifact_refs=written_paths,
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


def _build_messages(
    *,
    request: Request,
    work_step: WorkStep,
    workspace_overview: str | None = None,
) -> list[dict[str, str]]:
    expected = "\n".join(f"- {item}" for item in (work_step.expected_outputs or []))
    user_block = f"Request intent:\n{request.intent}\n\nWork step: {work_step.name}\nObjective: {work_step.objective}\n"
    if expected:
        user_block += f"Expected outputs:\n{expected}\n"
    if workspace_overview:
        user_block += f"\nWorkspace contents (top-level):\n{workspace_overview}\n"
    return [
        {
            "role": "system",
            "content": (
                "You execute a single coding work step for an AI company. The user can only "
                "verify your work via files on disk and the verifier command (usually pytest). "
                "RULES:\n"
                "1. You MUST use file_write at least once to create or modify a code file. "
                "Reading and listing alone are not a deliverable.\n"
                "2. Plan briefly, then act: pick a target path, write the file, run the verifier "
                "via shell_exec if useful, then send a one-paragraph plain-text summary (no tool "
                "calls) to hand off to the verifier.\n"
                "3. Do not respond with prose explaining what you would do — do it with tools first.\n"
                "4. If the workspace is empty or sparse, that's expected; create the file you need "
                "(for example backend/src/api/health.py for a /healthz endpoint).\n"
                "5. Stay inside the workspace. Path traversal and destructive shell commands are "
                "blocked at the tool boundary.\n"
                "6. PRESERVE existing tests and code. If a test file or module already exists in the "
                "workspace, file_read it first and ADD to it — do not rewrite the file from scratch "
                "and do not delete tests that cover behaviour outside this work step's scope. "
                "Overwriting prior work breaks the cumulative dogfooding loop.\n"
                "7. FIX FAILING TESTS BEFORE ADDING NEW CODE. If pytest (or any verifier) reports a "
                "failure, read the failure, fix the code OR the test, and re-run BEFORE moving on. "
                "Do not write more new files while existing tests are still red — the verifier "
                "blocks the deliverable on a single failure regardless of how much else is added.\n"
                "8. PYTHON PACKAGING — when authoring pyproject.toml:\n"
                "   - NEVER declare standard-library modules as dependencies (sqlite3, json, os, "
                "datetime, uuid, hashlib, logging, asyncio, etc. ship with Python — pip cannot "
                "install them and the install_smoke aspect will fail).\n"
                "   - Prefer PEP 621 (``[project]`` section) over Poetry-only (``[tool.poetry]``). "
                "PEP 621 makes ``pip install -e .`` install deps; Poetry-only needs ``poetry``.\n"
                "   - When writing a Dockerfile, install deps via the pyproject (``COPY pyproject.toml "
                "./`` then ``RUN pip install --no-cache-dir .``), NOT hardcoded ``pip install fastapi``. "
                "Hardcoded lists silently desync from pyproject.\n"
                "9. DOCKER COMPOSE — when authoring docker-compose.yml:\n"
                "   - OMIT the top-level ``version:`` key. It's obsolete in Compose v2 and produces a "
                "warning. Just start with ``services:``.\n"
                "   - For SQLite or any single-file persistence, mount a *directory* and put the file "
                "inside it (``./data:/app/data``), or use a named volume. Mounting a non-existent host "
                "file creates a directory on the host with that name, breaking the app.\n"
                "10. WORKSPACE PYTHON ENV — the workspace's Python interpreter (``python`` / "
                "``python3``) already has fastapi, uvicorn, httpx, pytest, pytest-asyncio, ruff, "
                "sqlalchemy and asyncpg installed. Run ``python -m pytest`` and "
                "``python -m ruff check .`` directly. DO NOT run ``pip install`` — the deps are "
                "already there, install attempts only waste rounds (they may also fail because the "
                "verifier env doesn't have setuptools wired for editable installs).\n"
                "11. FINAL VERIFICATION SWEEP — before sending your plain-text summary (which exits "
                "the loop), you MUST have run EACH of these commands in the last few rounds, ALL "
                "with exit 0:\n"
                "   (a) ``python -m pytest`` — every test passes\n"
                "   (b) ``python -m ruff check .`` — zero lint errors (fix with ``ruff check . --fix`` "
                "or manually; F401 unused-import is the most common qwen-flavoured miss)\n"
                "   (c) ``python -m ruff format --check .`` — zero format diffs (fix with "
                "``python -m ruff format .`` then re-check). DO NOT skip this — ``ruff check`` and "
                "``ruff format`` are separate tools; passing one doesn't mean the other passes.\n"
                "The deliverable is auto-rejected by code_test / code_lint / code_install_smoke if "
                "ANY of (a)-(c) is dirty when you summarize. Skip a check and the entire Direction "
                "fails to ship.\n"
                "12. FLAT-LAYOUT SETUPTOOLS TRAP — when your pyproject.toml uses setuptools and your "
                "code is at the workspace root (e.g. ``app.py`` and ``test_app.py`` next to each "
                "other, no ``src/`` dir), setuptools 67+ refuses ``pip install -e .`` with \"Multiple "
                "top-level modules discovered in a flat-layout\". Add this to pyproject.toml:\n"
                "   ``[tool.setuptools]``\n"
                "   ``py_modules = [\"app\"]``  # the single module that IS the package\n"
                "This tells setuptools which file is the package and excludes test_app.py from the "
                "build. Required for any flat-layout install_smoke to pass."
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


# G6.7 — pass a short workspace tree summary in the first user
# message so the model doesn't burn rounds listing files just to learn
# what exists. Recursive, ~120 entries, depth 3, ignores cache dirs.
_OVERVIEW_IGNORE = {".git", "__pycache__", ".pytest_cache", ".venv", "node_modules", "dist", "build"}
_OVERVIEW_MAX_ENTRIES = 120
_OVERVIEW_MAX_DEPTH = 3
_OVERVIEW_MAX_PREVIEW_FILES = 10
_OVERVIEW_PREVIEW_CHARS = 900
_OVERVIEW_PREVIEW_SUFFIXES = {".md", ".py", ".toml", ".json", ".txt", ".yaml", ".yml"}


def _workspace_overview(workspace_dir: Path | str | None) -> str:
    if workspace_dir is None:
        return ""
    root = Path(workspace_dir)
    if not root.exists():
        return ""
    entries: list[str] = []
    preview_paths: list[Path] = []

    def _walk(current: Path, depth: int) -> None:
        if depth > _OVERVIEW_MAX_DEPTH or len(entries) >= _OVERVIEW_MAX_ENTRIES:
            return
        try:
            children = sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return
        for child in children:
            if child.name in _OVERVIEW_IGNORE or child.name.startswith("."):
                continue
            relative = child.relative_to(root).as_posix()
            entries.append(relative + ("/" if child.is_dir() else ""))
            if (
                child.is_file()
                and len(preview_paths) < _OVERVIEW_MAX_PREVIEW_FILES
                and child.suffix in _OVERVIEW_PREVIEW_SUFFIXES
            ):
                preview_paths.append(child)
            if len(entries) >= _OVERVIEW_MAX_ENTRIES:
                return
            if child.is_dir():
                _walk(child, depth + 1)

    _walk(root, 1)
    if not entries:
        return "(workspace is empty — you will be creating files from scratch)"
    overview = ["Tree:", *entries]
    if preview_paths:
        overview.append("")
        overview.append("Seed file previews:")
        for path in preview_paths:
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            except OSError:
                continue
            relative = path.relative_to(root).as_posix()
            preview = content[:_OVERVIEW_PREVIEW_CHARS]
            if len(content) > _OVERVIEW_PREVIEW_CHARS:
                preview += "\n...<truncated>"
            overview.append(f"--- {relative} ---")
            overview.append(preview)
    return "\n".join(overview)


class _ToolLoopTerminated(Exception):
    """Raised inside the work-phase loop when ``record_tool_event``
    fires a terminal reason (budget / repetition). The outer handler
    transitions the work step and returns the dispatcher result.

    ``written_paths`` carries any ``file_write`` targets that *did*
    land before termination so the dispatcher can surface them as a
    ``human_review_required`` deliverable instead of dropping the work
    silently.
    """

    def __init__(
        self,
        *,
        attempt: RunAttempt,
        reason: str,
        written_paths: list[str] | None = None,
        final_text: str = "",
    ) -> None:
        super().__init__(reason)
        self.attempt = attempt
        self.reason = reason
        self.written_paths = list(written_paths or [])
        self.final_text = final_text


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
) -> tuple[str, list[str]]:
    """Tool-call loop for the work phase. Returns ``(final_text,
    written_paths)`` — the model's final plain-text response (persisted
    as the deliverable summary) and the de-duplicated, first-seen-order
    list of every ``file_write`` target (persisted as the deliverable's
    ``artifact_refs`` so the verifier and the G8.2 commit step know
    what the run produced).

    The loop is bounded by both the per-phase ``record_tool_event``
    budget (already wired into the phase machine) and
    ``MAX_WORK_LOOP_ITERATIONS`` as an outer safety net.
    """
    work_tools = list(ALLOWED_TOOLS_BY_PHASE[RunAttemptPhase.work])
    tools_schema = tool_registry.schema_for(work_tools) if tool_registry is not None else None
    workspace_dir_str = str(workspace_dir) if workspace_dir is not None else None
    final_text = ""
    written_paths: list[str] = []
    no_work_nudges = 0

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

        if tool_registry is None:
            return final_text, written_paths

        if not tool_calls:
            if written_paths:
                return final_text, written_paths
            if no_work_nudges < MAX_NO_WORK_NUDGES:
                no_work_nudges += 1
                messages.append({"role": "assistant", "content": final_text or "(no tool calls)"})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "You have not created or modified any file yet. A prose answer is not a deliverable. "
                            "Use file_write now to create or modify the required file, then run shell_exec if a "
                            "verifier is available. If you need context, use file_read or file_list first."
                        ),
                    }
                )
                continue
            await finish_run_attempt(
                attempt=attempt,
                status=RunAttemptStatus.failed,
                terminal_reason="failed_nonconvergent:no_workspace_write",
                session=session,
            )
            raise _ToolLoopTerminated(attempt=attempt, reason="failed_nonconvergent:no_workspace_write")

        messages.append(_assistant_tool_call_message(final_text, tool_calls))

        for call in tool_calls:
            tool_name = str(call.get("name") or "")
            call_id = str(call.get("id") or "")
            arguments = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}

            tool_output, exit_code, writes = await _invoke_tool_safely(tool_registry, tool_name, arguments)
            if tool_name == "file_write" and exit_code == 0 and writes:
                for path in writes:
                    if path not in written_paths:
                        written_paths.append(path)
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
                raise _ToolLoopTerminated(
                    attempt=attempt,
                    reason=f"tool_event_error:{exc.__class__.__name__}",
                    written_paths=written_paths,
                    final_text=final_text,
                ) from exc

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
                    written_paths=written_paths,
                    final_text=final_text,
                )
            if event_result.nudge:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"{event_result.nudge} You have already seen this tool result. "
                            "Do not repeat the same read/list call. If no file has been written yet, "
                            "use file_write with the required change now; otherwise send the final summary."
                        ),
                    }
                )

    # Outer cap hit without convergence — terminate.
    await finish_run_attempt(
        attempt=attempt,
        status=RunAttemptStatus.timed_out,
        terminal_reason="work_loop_iteration_cap",
        session=session,
    )
    raise _ToolLoopTerminated(
        attempt=attempt,
        reason="work_loop_iteration_cap",
        written_paths=written_paths,
        final_text=final_text,
    )


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
