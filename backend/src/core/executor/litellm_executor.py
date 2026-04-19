"""LiteLLM Executor — streaming agentic loop with tool_use support.

Runs the LLM → tool_calls → execute tools → feed results → repeat cycle.
Uses ``stream=True`` for real-time text delta / STATUS marker detection.

All LLM calls go through litellm.acompletion (provider-agnostic).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

import litellm
import os

import structlog

from backend.src.tools.base import ToolCall, ToolDefinition, ToolResult
from backend.src.tools.cancellation import CancellationToken
from backend.src.tools.handler import ToolHandler

logger = structlog.get_logger(__name__)

# Per-acompletion timeout. Local 30B models under contention can easily take
# 200+s for a single tool-using turn, so default generously.
REQUEST_TIMEOUT = int(os.getenv("LLM_REQUEST_TIMEOUT", "600"))
LLM_RETRY_ON_TIMEOUT = 1  # retry once on timeout/connection errors

_STATUS_RE = re.compile(r"\[STATUS\s+(.+?)\]")


async def _emit(callback: Callable, event: "ExecutionEvent") -> None:
    """Call an event callback, awaiting if it returns a coroutine."""
    try:
        result = callback(event)
        if inspect.isawaitable(result):
            await result
    except Exception:
        pass  # Best-effort — don't break the agentic loop


@dataclass
class TokenUsage:
    """Accumulated token usage across all iterations."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def add(self, response: Any) -> None:
        usage = getattr(response, "usage", None)
        if usage:
            self.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
            self.completion_tokens += getattr(usage, "completion_tokens", 0) or 0
            self.total_tokens += getattr(usage, "total_tokens", 0) or 0

    def add_stream_usage(self, chunk: Any) -> None:
        """Extract usage from a streaming chunk (some providers send it on the last chunk)."""
        usage = getattr(chunk, "usage", None)
        if usage:
            self.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
            self.completion_tokens += getattr(usage, "completion_tokens", 0) or 0
            self.total_tokens += getattr(usage, "total_tokens", 0) or 0


@dataclass
class ExecutionEvent:
    """Event emitted during execution for SSE streaming."""

    type: str  # "text_delta", "status_update", "tool_start", "tool_end", "iteration", "done"
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionResult:
    """Final result of an executor run."""

    content: str
    tool_calls_made: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""
    stop_reason: str = "end_turn"
    iterations: int = 0


class LiteLLMExecutor:
    """Agentic loop executor using LiteLLM with streaming.

    Runs the LLM → tool_use → execute → repeat cycle until the LLM
    produces a final text response or limits are reached.
    """

    async def execute(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition] | None,
        tool_handler: ToolHandler | None,
        *,
        model: str,
        api_key: str,
        base_url: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        max_iterations: int = 10,
        project_id: uuid.UUID | None = None,
        on_event: Callable[[ExecutionEvent], Any] | None = None,
    ) -> ExecutionResult:
        """Run the agentic loop with streaming."""
        litellm_tools = [t.to_dict() for t in tools] if tools else None

        usage = TokenUsage()
        all_tool_calls: list[ToolCall] = []
        all_tool_results: list[ToolResult] = []
        messages = list(messages)

        for iteration in range(max_iterations):
            if project_id and CancellationToken.is_cancelled(project_id):
                logger.info("execution_cancelled", project_id=str(project_id), iteration=iteration)
                return ExecutionResult(
                    content="[작업이 중지되었습니다]",
                    tool_calls_made=all_tool_calls,
                    tool_results=all_tool_results,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                    total_tokens=usage.total_tokens,
                    model=model,
                    stop_reason="cancelled",
                    iterations=iteration,
                )

            if on_event:
                await _emit(on_event, ExecutionEvent("iteration", {"iteration": iteration}))

            # ── Stream LLM response ─────────────────────────────────
            content, tool_calls_raw, finish_reason = await self._stream_llm_call(
                messages=messages,
                model=model,
                api_key=api_key,
                base_url=base_url,
                litellm_tools=litellm_tools,
                temperature=temperature,
                max_tokens=max_tokens,
                usage=usage,
                iteration=iteration,
                on_event=on_event,
            )

            # ── No tool calls → final response ──────────────────────
            if not tool_calls_raw:
                if not content.strip() and all_tool_calls:
                    for prev_msg in reversed(messages):
                        if prev_msg.get("role") == "assistant" and prev_msg.get("content", "").strip():
                            content = prev_msg["content"]
                            break

                try:
                    cost_usd = litellm.cost_per_token(
                        model=model,
                        prompt_tokens=usage.prompt_tokens,
                        completion_tokens=usage.completion_tokens,
                    )
                    cost_usd = sum(cost_usd) if isinstance(cost_usd, tuple) else float(cost_usd)
                except Exception:
                    cost_usd = 0.0

                if on_event:
                    await _emit(on_event, ExecutionEvent("done", {
                        "content": content, "finish_reason": finish_reason,
                    }))

                return ExecutionResult(
                    content=content,
                    tool_calls_made=all_tool_calls,
                    tool_results=all_tool_results,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                    total_tokens=usage.total_tokens,
                    cost_usd=cost_usd,
                    model=model,
                    stop_reason=finish_reason or "stop",
                    iterations=iteration + 1,
                )

            # ── Process tool calls ───────────────────────────────────
            if not tool_handler:
                logger.warning("tool_calls_without_handler", model=model)
                return ExecutionResult(
                    content=content,
                    tool_calls_made=all_tool_calls,
                    model=model,
                    stop_reason="no_handler",
                    iterations=iteration + 1,
                )

            MAX_TOOL_CALLS_PER_ITERATION = 10
            parsed_calls: list[ToolCall] = []
            for tc_raw in tool_calls_raw[:MAX_TOOL_CALLS_PER_ITERATION]:
                call_id = tc_raw.get("id") or str(uuid.uuid4())
                func = tc_raw.get("function", {})
                try:
                    call_input = json.loads(func.get("arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    call_input = {}
                parsed_calls.append(ToolCall(id=call_id, name=func.get("name", ""), input=call_input))

            if len(tool_calls_raw) > MAX_TOOL_CALLS_PER_ITERATION:
                logger.warning("tool_calls_truncated",
                               requested=len(tool_calls_raw),
                               kept=MAX_TOOL_CALLS_PER_ITERATION,
                               model=model)

            all_tool_calls.extend(parsed_calls)

            for pc in parsed_calls:
                if on_event:
                    await _emit(on_event, ExecutionEvent("tool_start", {"tool": pc.name, "input": pc.input}))

            results = await tool_handler.execute_batch(parsed_calls)
            all_tool_results.extend(results)

            if project_id and CancellationToken.is_cancelled(project_id):
                logger.info("execution_cancelled_after_tools", project_id=str(project_id), iteration=iteration)
                return ExecutionResult(
                    content="[작업이 중지되었습니다]",
                    tool_calls_made=all_tool_calls,
                    tool_results=all_tool_results,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                    total_tokens=usage.total_tokens,
                    model=model,
                    stop_reason="cancelled",
                    iterations=iteration + 1,
                )

            for r in results:
                if on_event:
                    await _emit(on_event, ExecutionEvent("tool_end", {
                        "tool_call_id": r.tool_call_id,
                        "is_error": r.is_error,
                        "content_preview": r.content[:200] if r.content else "",
                    }))

            # Append assistant + tool results to conversation
            assistant_msg: dict[str, Any] = {"role": "assistant", "content": content}
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": tc.get("function", {}).get("name", ""),
                        "arguments": tc.get("function", {}).get("arguments", "{}"),
                    },
                }
                for tc in tool_calls_raw
            ]
            messages.append(assistant_msg)

            for result in results:
                messages.append({
                    "role": "tool",
                    "tool_call_id": result.tool_call_id,
                    "content": result.content,
                })

            logger.info("agentic_loop_iteration", model=model, iteration=iteration, tool_calls=len(parsed_calls))

        # Max iterations reached
        logger.warning("max_iterations_reached", model=model, max_iterations=max_iterations)
        return ExecutionResult(
            content="[최대 반복 횟수에 도달했습니다]",
            tool_calls_made=all_tool_calls,
            tool_results=all_tool_results,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            cost_usd=0.0,
            model=model,
            stop_reason="max_iterations",
            iterations=max_iterations,
        )

    async def _stream_llm_call(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str,
        api_key: str,
        base_url: str | None,
        litellm_tools: list | None,
        temperature: float,
        max_tokens: int,
        usage: TokenUsage,
        iteration: int,
        on_event: Callable[[ExecutionEvent], Any] | None,
    ) -> tuple[str, list[dict[str, Any]], str]:
        """Stream a single LLM call, accumulating content and tool calls.

        Returns ``(content, tool_calls_raw, finish_reason)``.

        ``tool_calls_raw`` is a list of dicts like:
        ``[{"id": "...", "function": {"name": "...", "arguments": "..."}}]``
        """
        stream = None
        for attempt in range(1 + LLM_RETRY_ON_TIMEOUT):
            try:
                stream = await litellm.acompletion(
                    model=model,
                    messages=messages,
                    tools=litellm_tools,
                    api_key=api_key,
                    api_base=base_url,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=REQUEST_TIMEOUT,
                    stream=True,
                    stream_options={"include_usage": True},
                )
                break
            except Exception as e:
                err_str = str(e).lower()
                is_transient = any(kw in err_str for kw in ("timeout", "connect", "refused", "reset", "eof"))
                if is_transient and attempt < LLM_RETRY_ON_TIMEOUT:
                    logger.warning("litellm_call_timeout_retry",
                                   model=model, iteration=iteration, attempt=attempt, error=str(e))
                    await asyncio.sleep(5)
                    continue
                logger.error("litellm_call_failed", model=model, iteration=iteration, error=str(e))
                raise

        # Accumulate streamed response
        content_parts: list[str] = []
        accumulated_text = ""
        tool_calls_acc: dict[int, dict[str, Any]] = {}  # index → {id, function: {name, arguments}}
        finish_reason = "stop"
        emitted_statuses: set[str] = set()

        async for chunk in stream:
            choice = chunk.choices[0] if chunk.choices else None
            if not choice:
                usage.add_stream_usage(chunk)
                continue

            delta = choice.delta
            if choice.finish_reason:
                finish_reason = choice.finish_reason

            # Text content delta
            if hasattr(delta, "content") and delta.content:
                content_parts.append(delta.content)
                accumulated_text += delta.content

                if on_event:
                    await _emit(on_event, ExecutionEvent("text_delta", {"text": delta.content}))

                # Real-time STATUS marker detection
                for m in _STATUS_RE.finditer(accumulated_text):
                    status_text = m.group(1)
                    if status_text not in emitted_statuses:
                        emitted_statuses.add(status_text)
                        if on_event:
                            await _emit(on_event, ExecutionEvent("status_update", {"text": status_text}))

            # Tool call deltas
            if hasattr(delta, "tool_calls") and delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index if hasattr(tc_delta, "index") else 0
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {
                            "id": "",
                            "function": {"name": "", "arguments": ""},
                        }
                    entry = tool_calls_acc[idx]
                    if hasattr(tc_delta, "id") and tc_delta.id:
                        entry["id"] = tc_delta.id
                    func_delta = getattr(tc_delta, "function", None)
                    if func_delta:
                        if hasattr(func_delta, "name") and func_delta.name:
                            entry["function"]["name"] += func_delta.name
                        if hasattr(func_delta, "arguments") and func_delta.arguments:
                            entry["function"]["arguments"] += func_delta.arguments

            # Usage on last chunk
            usage.add_stream_usage(chunk)

        content = "".join(content_parts)
        tool_calls_raw = [tool_calls_acc[idx] for idx in sorted(tool_calls_acc)]

        # Filter out empty tool calls (some providers send empty deltas)
        tool_calls_raw = [tc for tc in tool_calls_raw if tc["function"]["name"]]

        return content, tool_calls_raw, finish_reason
