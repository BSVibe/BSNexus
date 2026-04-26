"""Bridge between ``RunOrchestrator`` and ``litellm.acompletion``.

Runs a tool-calling loop: each turn the model may request one or more
``file_write`` / ``file_read`` / ``file_list`` calls; we execute them
against the project workspace and feed the results back on the next
turn. The loop terminates when the assistant replies without tool
calls, or when a safety cap is hit.

The adapter owns no persistence logic itself — it only relays tool
requests to ``core.tools`` and gathers the written-file log so
``run_artifacts.publish_run_output`` can create a Deliverable without
having to re-parse the chat reply.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Any

import litellm
import structlog

from backend.src.core.tools import ToolRunLog, execute_tool_call, tool_schemas

logger = structlog.get_logger(__name__)

REQUEST_TIMEOUT = int(os.getenv("LLM_REQUEST_TIMEOUT", "600"))

# Wall-clock budget for one ``execute()`` call across all tool-loop
# iterations. Backstop in case individual ``REQUEST_TIMEOUT`` slips
# (Ollama via litellm is known to ignore the ``timeout`` kwarg under
# certain provider/version combos — the run silently hangs forever).
# 30 minutes is enough for legitimate 8-10 file scaffolds even with a
# slow local LLM; anything past this is stuck.
EXECUTE_WALL_CLOCK_S = int(os.getenv("LLM_EXECUTE_WALL_CLOCK_S", "1800"))

# When the model streams tokens, we keep a sliding watchdog: if no new
# chunk arrives for this many seconds, assume the upstream stalled and
# abort the iteration. Phase 2 of the previous E2E run hung for 10+
# minutes after Ollama finished generating because the connection
# silently stopped without an error frame; the wall-clock timeout
# masked it. This catches it within 90s.
NO_PROGRESS_TIMEOUT_S = int(os.getenv("LLM_NO_PROGRESS_TIMEOUT_S", "90"))

# Stream the LLM response by default — see commit message. Disable with
# LLM_STREAMING=0 if a provider has poor streaming support.
STREAMING_ENABLED = os.getenv("LLM_STREAMING", "1") not in ("0", "false", "False")

# Models known to emit malformed JSON in streamed tool_call deltas
# (multiple objects concatenated in a single chunk, no separator —
# litellm's parser raises ``APIConnectionError: Extra data ...``). For
# these we fall back to non-streaming. Empirically: every Ollama model
# we've tried with tool calling has this issue (glm-4.7-flash,
# qwen3-coder:30b). Prefix match.
_NO_STREAM_MODEL_PREFIXES: tuple[str, ...] = (
    "ollama/",
    "ollama_chat/",
)


def _model_supports_streaming(model: str) -> bool:
    if not STREAMING_ENABLED:
        return False
    return not model.startswith(_NO_STREAM_MODEL_PREFIXES)


# Maximum number of assistant↔tool turns in a single ``execute``. Each
# iteration lets the model issue another batch of tool calls. Need
# headroom for: read context files (3-4 turns) → write N files (N turns)
# → run verification command (1-2 turns) → fix-up loop (3 turns). 24 is
# the sweet spot — high enough that legitimate multi-file scaffolds
# (≈ 8-10 files) finish cleanly, low enough that the
# write-the-same-file-forever loops weak local LLMs sometimes fall
# into still get capped within a couple of minutes.
MAX_TOOL_ITERATIONS = int(os.getenv("LLM_MAX_TOOL_ITERATIONS", "24"))


def build_run_audit_metadata(
    *,
    run: Any,
    snapshot: Any | None,
) -> dict[str, Any]:
    """Build the ``metadata`` dict the LiteLLM hook in BSGateway will read.

    Cross-PR contract (Lockin §Architectural shifts #1) — keys mirror what
    BSGateway's ``async_pre_call_hook`` consumes to forward
    ``run.pre`` / ``run.post`` events to BSupervisor on BSNexus's behalf:

    * ``tenant_id`` — UUID string of the run's tenant
    * ``run_id`` — UUID string of this ExecutionRun
    * ``request_id`` — UUID string or None (some runs have no parent Request)
    * ``parent_run_id`` — UUID string or None (top-level run vs successor)
    * ``agent_name`` — persona / agent label for observability grouping
    * ``cost_estimate`` — pre-run cost estimate in USD cents (None when not set)

    Drift in this dict's shape between BSNexus and BSGateway breaks the
    audit trail — the regression test
    ``tests/test_orchestrator_adapter_audit_metadata.py`` pins it.
    """
    request_id = getattr(run, "request_id", None)
    parent_run_id = getattr(run, "parent_run_id", None)
    agent_name = getattr(snapshot, "persona_label", None) if snapshot is not None else None
    cost_estimate = getattr(snapshot, "cost_estimate_cents", None) if snapshot is not None else None
    return {
        "tenant_id": str(run.tenant_id),
        "run_id": str(run.id),
        "request_id": str(request_id) if request_id is not None else None,
        "parent_run_id": str(parent_run_id) if parent_run_id is not None else None,
        "agent_name": agent_name,
        "cost_estimate": cost_estimate,
    }


class LiteLLMOrchestratorAdapter:
    """Orchestrator-facing adapter over ``litellm.acompletion``."""

    tools_supported: list[str] = [
        "file_read",
        "file_write",
        "file_list",
        "shell_exec",
    ]

    def __init__(
        self,
        *,
        model: str,
        project_id: uuid.UUID,
        api_key: str = "unused",
        base_url: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        run_audit_metadata: dict[str, Any] | None = None,
    ):
        self._model = model
        self._project_id = project_id
        self._api_key = api_key or "unused"
        self._base_url = base_url
        self._max_tokens = max_tokens
        self._temperature = temperature
        # Phase 0 P0.7 — passed through to ``litellm.acompletion(metadata=...)``
        # so BSGateway's pre/post hooks can correlate every LLM call with
        # the originating ExecutionRun (Lockin §Architectural shifts #1).
        # Adapter is also constructible without metadata so existing tests
        # and legacy callers keep working.
        self._run_audit_metadata: dict[str, Any] | None = dict(run_audit_metadata) if run_audit_metadata else None

    def set_run_audit_metadata(self, metadata: dict[str, Any] | None) -> None:
        """Update the metadata payload sent on subsequent ``execute``
        calls. Used by the dispatcher after a run+snapshot is loaded —
        ``build_adapter`` runs before we have a run id, so the metadata
        is plumbed through here just before ``adapter.execute(...)``.
        """
        self._run_audit_metadata = dict(metadata) if metadata else None

    async def execute(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        tools_allowed: list[str],
        history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        for turn in history or []:
            if turn.get("role") in ("user", "assistant") and turn.get("content"):
                messages.append({"role": turn["role"], "content": turn["content"]})
        messages.append({"role": "user", "content": user_prompt})

        tools = tool_schemas(tools_allowed)
        tool_log = ToolRunLog(project_id=self._project_id)

        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_cost_usd = 0.0
        final_text = ""
        stop_reason = "stop"

        deadline = asyncio.get_event_loop().time() + EXECUTE_WALL_CLOCK_S

        for iteration in range(MAX_TOOL_ITERATIONS):
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                logger.warning(
                    "llm_execute_wall_clock_exhausted",
                    model=self._model,
                    iteration=iteration,
                    written=len(tool_log.written),
                )
                stop_reason = "wall_clock_exhausted"
                final_text = final_text or "(execute wall-clock budget hit; partial output persisted)"
                break
            logger.info(
                "llm_iteration_start",
                model=self._model,
                project_id=str(self._project_id),
                iteration=iteration,
                messages=len(messages),
                remaining_s=int(remaining),
            )
            response = await self._complete(messages, tools)
            logger.info(
                "llm_iteration_returned",
                model=self._model,
                project_id=str(self._project_id),
                iteration=iteration,
            )
            total_prompt_tokens += _int_usage(response, "prompt_tokens")
            total_completion_tokens += _int_usage(response, "completion_tokens")
            total_cost_usd += _safe_cost(
                self._model,
                _int_usage(response, "prompt_tokens"),
                _int_usage(response, "completion_tokens"),
            )

            choice = _first_choice(response)
            stop_reason = getattr(choice, "finish_reason", None) or "stop"
            message = _message_of(choice)
            assistant_content = _content_of(message)
            tool_calls = _tool_calls_of(message)

            # Persist the assistant turn so the next tool-response
            # turn lines up (OpenAI chat-completion ordering rule).
            messages.append(_assistant_turn(assistant_content, tool_calls))

            if not tool_calls:
                final_text = (assistant_content or "").strip()
                break

            for tool_idx, call in enumerate(tool_calls):
                name = _call_name(call)
                args_raw = _call_arguments(call)
                logger.info(
                    "tool_call_start",
                    project_id=str(self._project_id),
                    iteration=iteration,
                    tool_idx=tool_idx,
                    name=name,
                    args_size=len(args_raw or ""),
                )
                result = await execute_tool_call(name=name, raw_arguments=args_raw, log=tool_log)
                logger.info(
                    "tool_call_done",
                    project_id=str(self._project_id),
                    iteration=iteration,
                    tool_idx=tool_idx,
                    name=name,
                    result_size=len(result or ""),
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": _call_id(call),
                        "name": name,
                        "content": result,
                    }
                )
        else:
            logger.warning(
                "llm_tool_loop_hit_cap",
                model=self._model,
                cap=MAX_TOOL_ITERATIONS,
                written=len(tool_log.written),
            )
            stop_reason = "tool_iterations_exhausted"
            final_text = final_text or "(tool loop exhausted; partial output persisted)"

        cents = int(round(total_cost_usd * 100))
        logger.info(
            "run_llm_completed",
            model=self._model,
            stop_reason=stop_reason,
            completion_tokens=total_completion_tokens,
            cost_cents=cents,
            tools_invoked=tool_log.invocations,
            files_written=len(tool_log.written),
            tool_errors=tool_log.errors,
        )
        return {
            "status": "done",
            "output_type": "text",
            "output_ref": {
                "inline": final_text,
                "files": [f.to_ref() for f in tool_log.written],
            },
            "actual_cost_cents": cents,
            "model": self._model,
            "stop_reason": stop_reason,
            "prompt_tokens": total_prompt_tokens,
            "completion_tokens": total_completion_tokens,
        }

    async def _complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Any:
        extra_kwargs: dict[str, Any] = {}
        if self._model.startswith(("ollama/", "ollama_chat/")):
            extra_kwargs["num_ctx"] = int(os.getenv("OLLAMA_NUM_CTX", "40960"))

        kwargs: dict[str, Any] = {
            "model": self._model,
            # Snapshot: ``messages`` keeps mutating across the tool loop;
            # the provider must see the turn sequence as it was at call time.
            "messages": list(messages),
            "api_key": self._api_key,
            "api_base": self._base_url,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
            "timeout": REQUEST_TIMEOUT,
            **extra_kwargs,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        # P0.7 — forward run-audit metadata via litellm's ``metadata`` kwarg
        # so BSGateway's async_pre_call_hook / async_post_call_hook can
        # read it. Omit the key entirely (not an empty dict) when the
        # adapter wasn't constructed with metadata, so legacy paths and
        # tests that assert on call kwargs don't see a stray field.
        if self._run_audit_metadata:
            kwargs["metadata"] = dict(self._run_audit_metadata)

        # Hard outer-bound timeout — litellm's ``timeout`` kwarg is not
        # reliably enforced for every backend (notably Ollama via
        # ``ollama_chat/*``); without this wrapper the coroutine can
        # silently await forever with no error and no way to recover.
        # 1.2× the soft timeout gives ~20% headroom over what we expect
        # litellm to enforce internally.
        outer_timeout = REQUEST_TIMEOUT * 1.2

        if not _model_supports_streaming(self._model):
            return await asyncio.wait_for(litellm.acompletion(**kwargs), timeout=outer_timeout)

        # Streaming: chunks arrive as they're generated. We aggregate
        # content + tool_call deltas into the same shape the
        # non-streaming path returns so the caller doesn't care.
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}
        stream = await asyncio.wait_for(litellm.acompletion(**kwargs), timeout=outer_timeout)
        # Tests stub ``litellm.acompletion`` to return a plain object that
        # already has the non-streaming shape. Detect that and pass it
        # through unchanged so the adapter stays unit-testable without
        # an async-generator stub.
        if not hasattr(stream, "__aiter__"):
            return stream
        return await _consume_stream(stream, model=self._model)


# ────────────────────── streaming aggregation ──────────────────────


@dataclass
class _StreamedToolCall:
    id: str = ""
    name: str = ""
    arguments: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments},
        }


@dataclass
class _StreamedChoice:
    content: str = ""
    tool_calls: dict[int, _StreamedToolCall] = field(default_factory=dict)
    finish_reason: str | None = None


@dataclass
class _StreamedResponse:
    """Non-stream-shaped result built from streamed deltas.

    Has just enough surface (``choices[0].message.content`` /
    ``choices[0].message.tool_calls`` / ``choices[0].finish_reason`` /
    ``usage``) to flow through the existing helpers in this module
    unchanged.
    """

    choice: _StreamedChoice
    usage: dict[str, int] = field(default_factory=dict)

    @property
    def choices(self) -> list[_FakeChoice]:
        return [_FakeChoice(self.choice)]


@dataclass
class _FakeChoice:
    inner: _StreamedChoice

    @property
    def finish_reason(self) -> str | None:
        return self.inner.finish_reason

    @property
    def message(self) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": "assistant", "content": self.inner.content}
        if self.inner.tool_calls:
            msg["tool_calls"] = [self.inner.tool_calls[i].to_dict() for i in sorted(self.inner.tool_calls.keys())]
        return msg


async def _consume_stream(stream: Any, *, model: str) -> _StreamedResponse:
    """Iterate the litellm stream, applying a no-progress watchdog.

    Cancels the underlying generator if no chunk arrives for
    ``NO_PROGRESS_TIMEOUT_S`` seconds. Raises ``asyncio.TimeoutError``
    in that case so the orchestrator records the run as blocked
    instead of hanging the worker forever.
    """
    state = _StreamedChoice()
    usage: dict[str, int] = {}

    aiter_obj = stream.__aiter__()
    while True:
        try:
            chunk = await asyncio.wait_for(aiter_obj.__anext__(), timeout=NO_PROGRESS_TIMEOUT_S)
        except StopAsyncIteration:
            break
        except asyncio.TimeoutError:
            logger.warning(
                "llm_stream_no_progress",
                model=model,
                timeout_s=NO_PROGRESS_TIMEOUT_S,
                content_len=len(state.content),
                tool_calls=len(state.tool_calls),
            )
            raise

        # Aggregate this chunk's deltas. litellm normalises chunks to an
        # OpenAI-shape with .choices[0].delta + optional .usage.
        choices = getattr(chunk, "choices", None) or []
        if choices:
            choice = choices[0]
            delta = getattr(choice, "delta", None) or {}
            content_delta = getattr(delta, "content", None) if not isinstance(delta, dict) else delta.get("content")
            if content_delta:
                state.content += content_delta

            tool_calls_delta = (
                getattr(delta, "tool_calls", None) if not isinstance(delta, dict) else delta.get("tool_calls")
            ) or []
            for tc in tool_calls_delta:
                idx = getattr(tc, "index", None) if not isinstance(tc, dict) else tc.get("index")
                idx = int(idx or 0)
                bucket = state.tool_calls.setdefault(idx, _StreamedToolCall())
                tc_id = getattr(tc, "id", None) if not isinstance(tc, dict) else tc.get("id")
                if tc_id:
                    bucket.id = str(tc_id)
                func = getattr(tc, "function", None) if not isinstance(tc, dict) else tc.get("function")
                if func is not None:
                    name = getattr(func, "name", None) if not isinstance(func, dict) else func.get("name")
                    if name:
                        bucket.name = str(name)
                    args = getattr(func, "arguments", None) if not isinstance(func, dict) else func.get("arguments")
                    if args:
                        bucket.arguments += str(args)

            finish = getattr(choice, "finish_reason", None)
            if finish:
                state.finish_reason = str(finish)

        chunk_usage = getattr(chunk, "usage", None)
        if chunk_usage:
            for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                v = getattr(chunk_usage, k, None) if not isinstance(chunk_usage, dict) else chunk_usage.get(k)
                if v is not None:
                    try:
                        usage[k] = int(v)
                    except (TypeError, ValueError):
                        pass

    return _StreamedResponse(choice=state, usage=usage)


# ────────────────────── response helpers ──────────────────────


def _first_choice(response: Any) -> Any:
    choices = getattr(response, "choices", None) or []
    if not choices:
        return None
    return choices[0]


def _message_of(choice: Any) -> Any:
    if choice is None:
        return {}
    return getattr(choice, "message", None) or {}


def _content_of(message: Any) -> str | None:
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    return content


def _tool_calls_of(message: Any) -> list[Any]:
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls is None and isinstance(message, dict):
        tool_calls = message.get("tool_calls")
    return tool_calls or []


def _call_name(call: Any) -> str:
    func = getattr(call, "function", None) or (call.get("function") if isinstance(call, dict) else None)
    if func is None:
        return ""
    name = getattr(func, "name", None)
    if name is None and isinstance(func, dict):
        name = func.get("name")
    return str(name or "")


def _call_arguments(call: Any) -> str:
    func = getattr(call, "function", None) or (call.get("function") if isinstance(call, dict) else None)
    if func is None:
        return ""
    args = getattr(func, "arguments", None)
    if args is None and isinstance(func, dict):
        args = func.get("arguments")
    if args is None:
        return ""
    if isinstance(args, dict):
        return json.dumps(args)
    return str(args)


def _call_id(call: Any) -> str:
    cid = getattr(call, "id", None)
    if cid is None and isinstance(call, dict):
        cid = call.get("id")
    return str(cid or "")


def _assistant_turn(content: str | None, tool_calls: list[Any]) -> dict[str, Any]:
    """Mirror the assistant turn back into the chat so OpenAI-compatible
    servers validate the sequence. Tool calls are serialised to plain
    dicts; some providers reject dataclass-style objects here.
    """
    turn: dict[str, Any] = {"role": "assistant", "content": content or ""}
    if tool_calls:
        turn["tool_calls"] = [
            {
                "id": _call_id(c),
                "type": "function",
                "function": {
                    "name": _call_name(c),
                    "arguments": _call_arguments(c),
                },
            }
            for c in tool_calls
        ]
    return turn


def _int_usage(response: Any, key: str) -> int:
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0
    value = getattr(usage, key, None)
    if value is None and isinstance(usage, dict):
        value = usage.get(key)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    try:
        cost = litellm.cost_per_token(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        if isinstance(cost, tuple):
            return float(sum(cost))
        return float(cost)
    except Exception:
        return 0.0
