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

import json
import os
import uuid
from typing import Any

import litellm
import structlog

from backend.src.core.tools import ToolRunLog, execute_tool_call, tool_schemas

logger = structlog.get_logger(__name__)

REQUEST_TIMEOUT = int(os.getenv("LLM_REQUEST_TIMEOUT", "600"))

# Maximum number of assistant↔tool turns in a single ``execute``. Each
# iteration lets the model issue another batch of tool calls. 12 is
# enough for ~1 dozen files of output while capping runaway loops.
MAX_TOOL_ITERATIONS = int(os.getenv("LLM_MAX_TOOL_ITERATIONS", "12"))


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
    ):
        self._model = model
        self._project_id = project_id
        self._api_key = api_key or "unused"
        self._base_url = base_url
        self._max_tokens = max_tokens
        self._temperature = temperature

    async def execute(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        tools_allowed: list[str],
        history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt}
        ]
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

        for iteration in range(MAX_TOOL_ITERATIONS):
            response = await self._complete(messages, tools)
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

            for call in tool_calls:
                name = _call_name(call)
                args_raw = _call_arguments(call)
                result = await execute_tool_call(
                    name=name, raw_arguments=args_raw, log=tool_log
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

    async def _complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> Any:
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

        return await litellm.acompletion(**kwargs)


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
    func = getattr(call, "function", None) or (
        call.get("function") if isinstance(call, dict) else None
    )
    if func is None:
        return ""
    name = getattr(func, "name", None)
    if name is None and isinstance(func, dict):
        name = func.get("name")
    return str(name or "")


def _call_arguments(call: Any) -> str:
    func = getattr(call, "function", None) or (
        call.get("function") if isinstance(call, dict) else None
    )
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
