"""``DirectLLMAdapter`` — ``executor_type=llm_api`` path via ``bsvibe_llm``.

CLAUDE.md two-path LLM dispatch:
  - ``executor_type=bsgateway`` → ``BSGatewayClient`` (BSGateway HTTP wire).
  - ``executor_type=llm_api``   → this module, which wraps
    :class:`bsvibe_llm.LlmClient` in ``direct=True`` mode.

Why ``bsvibe_llm`` instead of raw ``litellm``: the shared package owns
the retry policy, fallback chain, reasoning-suppression strategy, and
the audit-metadata wire contract that every BSVibe product agrees on.
Importing ``litellm`` from BSNexus directly bypasses all of that and
drifts the wire shape. The legacy-erasure suite enforces zero
``litellm`` imports anywhere under ``backend/src/``.

The class satisfies the
:class:`backend.src.core.executor_config.ExecutorClient` Protocol so
the resolver can return either client without callers branching.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from bsvibe_llm import CompletionResult, LlmClient, LlmSettings, RunAuditMetadata

logger = structlog.get_logger(__name__)


class DirectLLMError(Exception):
    """Raised when the underlying ``bsvibe_llm`` call fails.

    Mirrors ``BSGatewayError.partial_output`` (always empty for now —
    ``LlmClient.complete()`` is non-streaming so there's no partial
    text to surface; we keep the field for shape parity).
    """

    def __init__(self, message: str, *, partial_output: str = "") -> None:
        super().__init__(message)
        self.partial_output = partial_output


class DirectLLMAdapter:
    """Wraps :class:`bsvibe_llm.LlmClient` for the per-tenant direct path.

    One instance per per-tenant ``ExecutorConfig`` row.  The underlying
    ``LlmClient`` is instantiated lazily (or injected in tests) so
    construction stays cheap and the resolver can mint adapters without
    holding any network resources.
    """

    def __init__(
        self,
        *,
        base_url: str | None,
        api_key: str,
        client: LlmClient | None = None,
    ) -> None:
        # ``base_url`` is None when the SaaS provider's endpoint is
        # inferred from the model id (OpenAI, Anthropic, …); non-empty
        # for self-host runtimes (Ollama, vLLM).
        self._base_url = base_url
        self._api_key = api_key
        if client is not None:
            self._client = client
        else:
            # ``LlmSettings`` has no ``api_base`` / ``api_key`` fields and
            # inherits ``extra="ignore"`` — passing them here would be
            # silently dropped. The per-tenant endpoint + credential are
            # instead forwarded per-call via ``complete(extra=...)``
            # (see ``execute``); ``_resolve_api_base`` returns "" for
            # ``direct=True`` by design, leaving the caller in control.
            self._client = LlmClient(
                settings=LlmSettings(bsgateway_url="", route_default="direct"),
            )

    async def execute(
        self,
        *,
        messages: list[dict[str, Any]],
        metadata: dict[str, Any],
        model: str,
        workspace_dir: str | None = None,  # noqa: ARG002 — Protocol parity; tools run dispatcher-side
        mcp_servers: dict[str, Any] | None = None,  # noqa: ARG002 — MCP is a separate path (BSage / BSGateway side)
        tools: list[dict[str, Any]] | None = None,
        on_chunk: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """Run a single completion through ``bsvibe_llm`` (``direct=True``)
        and return the shared executor-client result shape.

        When ``tools`` is provided, the model may emit ``tool_calls``;
        they're surfaced verbatim in the result dict so the G6.6
        dispatcher tool loop can dispatch them. Streaming:
        ``LlmClient.complete()`` is non-streaming, so the adapter emits
        one ``on_chunk(text)`` after the full response arrives.
        """
        audit_metadata = _coerce_metadata(metadata)
        # ``extra`` is forwarded verbatim to ``litellm.acompletion``.
        # It's the only wire that reaches litellm for ``direct=True``
        # calls: ``bsvibe_llm._resolve_api_base`` returns "" for direct,
        # so a self-host runtime's endpoint must ride here or litellm
        # falls back to its ``localhost:11434`` default.
        extra: dict[str, Any] = {}
        if self._base_url:
            extra["api_base"] = self._base_url
        if self._api_key:
            extra["api_key"] = self._api_key
        try:
            result = await self._client.complete(
                messages=messages,
                metadata=audit_metadata,
                model=model,
                direct=True,
                tools=tools,
                extra=extra or None,
            )
        except Exception as exc:
            raise DirectLLMError(f"bsvibe_llm direct dispatch failed: {exc}") from exc

        text = result.text or ""
        if on_chunk is not None and text:
            await on_chunk(text)

        return {
            "output_type": "text",
            "output_ref": text,
            "actual_cost_cents": 0,
            "finish_reason": result.finish_reason,
            "tool_calls": _extract_tool_calls(result),
        }


def _coerce_metadata(payload: dict[str, Any]) -> RunAuditMetadata:
    """Translate the orchestrator's free-form metadata dict into the
    typed wire contract ``bsvibe_llm`` requires.

    ``tenant_id`` + ``run_id`` are required by the wire (BSGateway
    rejects anonymous traffic); the rest are best-effort.
    """
    tenant_id = payload.get("tenant_id")
    run_id = payload.get("run_id")
    if not tenant_id or not run_id:
        raise DirectLLMError("metadata must include both 'tenant_id' and 'run_id' for the direct LLM path")

    known = {
        "tenant_id",
        "run_id",
        "request_id",
        "parent_run_id",
        "agent_name",
        "cost_estimate_cents",
        "project_id",
        "composition_id",
    }
    extras = {k: v for k, v in payload.items() if k not in known}

    return RunAuditMetadata(
        tenant_id=str(tenant_id),
        run_id=str(run_id),
        request_id=_opt_str(payload.get("request_id")),
        parent_run_id=_opt_str(payload.get("parent_run_id")),
        agent_name=_opt_str(payload.get("agent_name")),
        cost_estimate_cents=_opt_int(payload.get("cost_estimate_cents")),
        project_id=_opt_str(payload.get("project_id")),
        composition_id=_opt_str(payload.get("composition_id")),
        extras=extras,
    )


def _opt_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _opt_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _extract_tool_calls(result: CompletionResult) -> list[dict[str, Any]] | None:
    """Pull OpenAI-style ``tool_calls`` off the underlying litellm
    response. Returns None when the model returned plain text.

    LiteLLM normalises to ``choices[0].message.tool_calls`` for
    both providers that support function calling and Ollama tool-call
    streaming. Arguments come as a JSON string per the OpenAI spec —
    we parse here so the dispatcher receives a typed dict.
    """
    raw = result.raw
    if raw is None:
        return None
    choices = getattr(raw, "choices", None) or []
    if not choices:
        return None
    message = getattr(choices[0], "message", None)
    if message is None:
        return None
    tool_calls = getattr(message, "tool_calls", None)
    if not tool_calls:
        return None

    extracted: list[dict[str, Any]] = []
    for call in tool_calls:
        call_id = str(getattr(call, "id", "") or "")
        function = getattr(call, "function", None)
        name = str(getattr(function, "name", "") or "")
        raw_args = getattr(function, "arguments", "") or ""
        try:
            parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        except (TypeError, ValueError):
            parsed_args = {"_raw_arguments": str(raw_args)}
        if not isinstance(parsed_args, dict):
            parsed_args = {"_raw_arguments": raw_args}
        extracted.append({"id": call_id, "name": name, "arguments": parsed_args})
    return extracted or None
