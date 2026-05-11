"""``DirectLLMAdapter`` — litellm direct path for ``executor_type=llm_api``.

CLAUDE.md two-path LLM dispatch (Phase 2b, BSVibe-optional):

  - ``executor_type=bsgateway`` → ``BSGatewayClient`` (HTTP wire to a
    BSGateway worker pool; tool loop lives inside the worker).
  - ``executor_type=llm_api``   → ``DirectLLMAdapter`` (this module;
    litellm + MCP tool loop client-side).

The ``execute()`` contract mirrors ``BSGatewayClient.execute()`` so
the resolver (``core.executor_config.resolve_executor``) returns
either and downstream callers (G6.3 RunAttempt executor) can dispatch
without branching.

litellm is imported only inside this package — see the
``test_litellm_is_fenced_to_core_llm`` legacy-erasure guard.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from litellm import acompletion

logger = structlog.get_logger(__name__)


class DirectLLMError(Exception):
    """Raised when the litellm async streaming call fails partway.

    Mirrors ``BSGatewayError.partial_output`` so the orchestrator can
    surface what the model produced before the failure (Inside panel
    consumers read this verbatim).
    """

    def __init__(self, message: str, *, partial_output: str = "") -> None:
        super().__init__(message)
        self.partial_output = partial_output


class DirectLLMAdapter:
    """Thin async wrapper around ``litellm.acompletion(..., stream=True)``.

    Single instance is safe to reuse across runs — every ``execute()``
    call spawns its own stream and the adapter holds no per-call state.
    """

    def __init__(
        self,
        *,
        base_url: str | None,
        api_key: str,
    ) -> None:
        # ``base_url`` is None when the SaaS provider's endpoint is
        # inferred from the model id (OpenAI, Anthropic, Mistral, …).
        # When set it routes to a self-hosted runtime (Ollama, vLLM)
        # via litellm's ``api_base`` kwarg.
        self._base_url = base_url
        self._api_key = api_key

    async def execute(
        self,
        *,
        messages: list[dict[str, Any]],
        metadata: dict[str, Any],
        model: str,
        workspace_dir: str | None = None,  # noqa: ARG002 — mirror BSGateway shape
        mcp_servers: dict[str, Any] | None = None,  # noqa: ARG002 — tool loop is PR-3 territory
        on_chunk: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """Stream-completion call. Return shape matches
        ``BSGatewayClient.execute()`` so the resolver-returned client is
        interchangeable from the caller's POV.
        """
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "api_key": self._api_key,
            "metadata": metadata,
        }
        if self._base_url:
            kwargs["api_base"] = self._base_url

        parts: list[str] = []
        finish_reason: str | None = None

        try:
            stream = await acompletion(**kwargs)
            async for chunk in stream:
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                choice = choices[0]
                delta = getattr(choice, "delta", None)
                content = getattr(delta, "content", None) if delta is not None else None
                if isinstance(content, str) and content:
                    parts.append(content)
                    if on_chunk is not None:
                        await on_chunk(content)
                fr = getattr(choice, "finish_reason", None)
                if fr:
                    finish_reason = fr
        except Exception as exc:
            raise DirectLLMError(
                f"litellm direct dispatch failed: {exc}",
                partial_output="".join(parts),
            ) from exc

        return {
            "output_type": "text",
            "output_ref": "".join(parts),
            "actual_cost_cents": 0,
            "finish_reason": finish_reason,
        }
