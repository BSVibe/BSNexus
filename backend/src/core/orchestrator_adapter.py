"""Bridge between ``RunOrchestrator`` and ``litellm.acompletion``.

``RunOrchestrator.dispatch_run`` expects an executor with
``async execute(system_prompt, *, tools_allowed) -> dict`` semantics —
text-in, result-dict-out.

The adapter runs a single-turn, no-tool chat against the tenant's
default LLM. That's all the orchestrator needs to close the loop for
non-coding runs today. Tool-using runs will ship behind a richer
adapter once the tool/workspace subsystem lands; the protocol is
already compatible.

Goes directly through ``litellm.acompletion`` rather than wrapping
``LiteLLMExecutor`` because the latter pulls in a tool/handler module
that is not yet in this repo (leftover from the agent era).
"""

from __future__ import annotations

import os
from typing import Any

import litellm
import structlog

logger = structlog.get_logger(__name__)

# Match LiteLLMExecutor's default so local Ollama models don't time out
# on slow hosts.
REQUEST_TIMEOUT = int(os.getenv("LLM_REQUEST_TIMEOUT", "600"))


class LiteLLMOrchestratorAdapter:
    """Orchestrator-facing adapter over ``litellm.acompletion``."""

    tools_supported: list[str] = ["read", "write"]

    def __init__(
        self,
        *,
        model: str,
        api_key: str = "unused",
        base_url: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ):
        self._model = model
        self._api_key = api_key or "unused"
        self._base_url = base_url
        self._max_tokens = max_tokens
        self._temperature = temperature

    async def execute(
        self,
        system_prompt: str,
        *,
        tools_allowed: list[str],  # noqa: ARG002 — reserved for tool-capable adapter
    ) -> dict[str, Any]:
        extra_kwargs: dict[str, Any] = {}
        if self._model.startswith(("ollama/", "ollama_chat/")):
            extra_kwargs["num_ctx"] = int(os.getenv("OLLAMA_NUM_CTX", "40960"))

        response = await litellm.acompletion(
            model=self._model,
            messages=[{"role": "user", "content": system_prompt}],
            api_key=self._api_key,
            api_base=self._base_url,
            max_tokens=self._max_tokens,
            temperature=self._temperature,
            timeout=REQUEST_TIMEOUT,
            **extra_kwargs,
        )

        text, stop_reason = _extract_text(response)
        usage = getattr(response, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)

        cost_usd = _safe_cost(self._model, prompt_tokens, completion_tokens)
        cents = int(round(cost_usd * 100))

        logger.info(
            "run_llm_completed",
            model=self._model,
            stop_reason=stop_reason,
            completion_tokens=completion_tokens,
            cost_cents=cents,
        )
        return {
            "status": "done",
            "output_type": "text",
            "output_ref": {"inline": text},
            "actual_cost_cents": cents,
            "model": self._model,
            "stop_reason": stop_reason,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        }


def _extract_text(response: Any) -> tuple[str, str]:
    choices = getattr(response, "choices", None) or []
    if not choices:
        return "", "empty"
    choice = choices[0]
    message = getattr(choice, "message", None) or {}
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    stop_reason = getattr(choice, "finish_reason", None) or "stop"
    return (content or "").strip(), stop_reason


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
