"""LiteLLM Executor — agentic loop with tool_use support.

Consolidates claude_api, generic_llm, and bsgateway executors into
a single implementation that supports the full tool_use cycle:
  LLM call → tool_calls → execute tools → feed results → repeat

All LLM calls go through litellm.acompletion (provider-agnostic).
"""

from __future__ import annotations

import inspect
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

import litellm
import structlog

from backend.src.tools.base import ToolCall, ToolDefinition, ToolResult
from backend.src.tools.cancellation import CancellationToken
from backend.src.tools.handler import ToolHandler

logger = structlog.get_logger(__name__)

REQUEST_TIMEOUT = 600  # seconds per acompletion call (local models can be slow)


async def _emit(callback: Callable, event: ExecutionEvent) -> None:
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


@dataclass
class ExecutionEvent:
    """Event emitted during execution for SSE streaming."""

    type: str  # "text_delta", "tool_start", "tool_end", "iteration", "done"
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
    stop_reason: str = "end_turn"  # "end_turn" | "max_tokens" | "max_iterations" | "cancelled"
    iterations: int = 0


class LiteLLMExecutor:
    """Agentic loop executor using LiteLLM.

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
        max_iterations: int = 25,
        project_id: uuid.UUID | None = None,
        on_event: Callable[[ExecutionEvent], Any] | None = None,
    ) -> ExecutionResult:
        """Run the agentic loop.

        Args:
            messages: Conversation history (system + user + assistant).
            tools: Tool definitions to send to the LLM. None = no tools.
            tool_handler: Executes tool calls. Required if tools provided.
            model: LiteLLM model identifier.
            api_key: API key for the provider.
            base_url: Optional API base URL override.
            temperature: Sampling temperature.
            max_tokens: Max tokens per LLM response.
            max_iterations: Cap on agentic loop iterations.
            project_id: For cancellation token checks.
            on_event: Callback for streaming events (SSE).
        """
        # Convert tool definitions to LiteLLM format
        litellm_tools = [t.to_dict() for t in tools] if tools else None

        usage = TokenUsage()
        all_tool_calls: list[ToolCall] = []
        all_tool_results: list[ToolResult] = []
        # Work with a copy so we don't mutate the caller's list
        messages = list(messages)

        for iteration in range(max_iterations):
            # Check cancellation
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

            # Call LLM
            try:
                # Thinking/reasoning mode is disabled via /no_think prefix
                # in the system prompt (harness.py). vLLM-MLX does not support
                # chat_template_kwargs passthrough, so extra_body is not used.
                response = await litellm.acompletion(
                    model=model,
                    messages=messages,
                    tools=litellm_tools,
                    api_key=api_key,
                    api_base=base_url,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=REQUEST_TIMEOUT,
                )
            except Exception as e:
                logger.error("litellm_call_failed", model=model, iteration=iteration, error=str(e))
                raise

            usage.add(response)

            choice = response.choices[0]
            finish_reason = choice.finish_reason or "stop"
            message = choice.message

            # Check for tool_calls
            tool_calls_raw = getattr(message, "tool_calls", None)

            if not tool_calls_raw or finish_reason not in ("tool_calls", "stop"):
                # Final response — no more tool calls.
                # Some local models (Qwen3) return empty content after tool_use.
                # If content is empty, synthesize from the last assistant text
                # that appeared alongside tool calls.
                content = message.content or ""
                if not content.strip() and all_tool_calls:
                    # Look for the most recent non-empty assistant content
                    for prev_msg in reversed(messages):
                        if prev_msg.get("role") == "assistant" and prev_msg.get("content", "").strip():
                            content = prev_msg["content"]
                            break

                # Calculate cost
                try:
                    cost_usd = litellm.completion_cost(completion_response=response)
                except Exception:
                    cost_usd = 0.0

                if on_event:
                    await _emit(on_event, ExecutionEvent("done", {"content": content, "finish_reason": finish_reason}))

                return ExecutionResult(
                    content=content,
                    tool_calls_made=all_tool_calls,
                    tool_results=all_tool_results,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                    total_tokens=usage.total_tokens,
                    cost_usd=cost_usd,
                    model=model,
                    stop_reason=finish_reason,
                    iterations=iteration + 1,
                )

            # Process tool calls
            if not tool_handler:
                logger.warning("tool_calls_without_handler", model=model)
                return ExecutionResult(
                    content=message.content or "",
                    tool_calls_made=all_tool_calls,
                    model=model,
                    stop_reason="no_handler",
                    iterations=iteration + 1,
                )

            # Parse tool calls from response (cap at 10 per iteration to
            # prevent MoE models from generating hundreds of calls at once).
            MAX_TOOL_CALLS_PER_ITERATION = 10
            parsed_calls: list[ToolCall] = []
            for tc in tool_calls_raw[:MAX_TOOL_CALLS_PER_ITERATION]:
                call_id = tc.id or str(uuid.uuid4())
                func = tc.function
                try:
                    call_input = json.loads(func.arguments) if isinstance(func.arguments, str) else func.arguments
                except json.JSONDecodeError:
                    call_input = {}
                parsed_calls.append(ToolCall(id=call_id, name=func.name, input=call_input))
            if len(tool_calls_raw) > MAX_TOOL_CALLS_PER_ITERATION:
                logger.warning("tool_calls_truncated",
                               requested=len(tool_calls_raw),
                               kept=MAX_TOOL_CALLS_PER_ITERATION,
                               model=model)

            all_tool_calls.extend(parsed_calls)

            # Emit tool start events
            for pc in parsed_calls:
                if on_event:
                    await _emit(on_event, ExecutionEvent("tool_start", {"tool": pc.name, "input": pc.input}))

            # Execute tools
            results = await tool_handler.execute_batch(parsed_calls)
            all_tool_results.extend(results)

            # Check cancellation after tool execution
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

            # Emit tool end events
            for r in results:
                if on_event:
                    await _emit(on_event, ExecutionEvent("tool_end", {
                        "tool_call_id": r.tool_call_id,
                        "is_error": r.is_error,
                        "content_preview": r.content[:200] if r.content else "",
                    }))

            # Append assistant message (with tool_calls) + tool results to conversation
            # Build assistant message dict that litellm expects
            assistant_msg: dict[str, Any] = {"role": "assistant", "content": message.content or ""}
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments if isinstance(tc.function.arguments, str) else json.dumps(tc.function.arguments),
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

            logger.info(
                "agentic_loop_iteration",
                model=model,
                iteration=iteration,
                tool_calls=len(parsed_calls),
            )

        # Max iterations reached
        logger.warning("max_iterations_reached", model=model, max_iterations=max_iterations)

        try:
            total_cost = litellm.completion_cost(completion_response=response)
        except Exception:
            total_cost = 0.0

        return ExecutionResult(
            content="[최대 반복 횟수에 도달했습니다]",
            tool_calls_made=all_tool_calls,
            tool_results=all_tool_results,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            cost_usd=total_cost,
            model=model,
            stop_reason="max_iterations",
            iterations=max_iterations,
        )
