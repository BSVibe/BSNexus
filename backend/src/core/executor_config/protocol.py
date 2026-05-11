"""``ExecutorClient`` Protocol — the single contract every per-tenant
LLM dispatch client must satisfy.

Per CLAUDE.md two-path LLM dispatch MUST rule, BSNexus has two
concrete paths today (``BSGatewayClient``, ``DirectLLMAdapter``), and
the founder may add more later (a fully-managed cloud key vault path,
a no-LLM mock path for offline demos, etc.). Callers
(G6.3 RunAttempt executor, the M0 harness bridge in G6.4) depend on
this Protocol, never on the concrete classes — so adding a new kind
is one new class + one resolver branch, not a Union widening across
the whole codebase.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ExecutorClient(Protocol):
    """Single async surface for per-tenant LLM dispatch.

    Implementations:
      - :class:`backend.src.core.bsgateway.client.BSGatewayClient`
      - :class:`backend.src.core.llm.DirectLLMAdapter`

    Return shape is the BSGateway result dict so downstream callers
    can treat both paths identically:

    .. code-block:: python

        {
            "output_type": "text",
            "output_ref": "...assembled assistant text...",
            "actual_cost_cents": int,
            "finish_reason": "stop" | "length" | "tool_calls" | None,
            "tool_calls": [
                {"id": "call_xxx", "name": "file_write",
                 "arguments": {"path": "...", "content": "..."}},
                ...
            ] | None,
        }

    ``tool_calls`` is None when the model returned plain text; it's a
    list when ``finish_reason == "tool_calls"``. Callers (G6.6
    dispatcher) drive the loop: invoke the tools, append the
    tool-result messages, and call ``execute`` again.
    """

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
    ) -> dict[str, Any]: ...
