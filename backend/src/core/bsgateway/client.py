"""``BSGatewayClient`` — httpx wrapper over BSGateway chat completions.

Replaces ``LiteLLMOrchestratorAdapter`` for the executor-model path:
BSNexus no longer runs an in-process LLM tool-loop. Instead each
ExecutionRun POSTs once to ``{base_url}/api/v1/chat/completions``,
streams the SSE chunks back, and stores the aggregated text as the
deliverable. Tool use happens inside the worker's CLI agent (claude /
codex / opencode) — BSNexus only sees the final stream.

Wire contract: see ``~/Docs/BSNexus_BSGateway_Integration_2026-05-03.md``
and ``BSGateway/docs/BSNEXUS_METADATA_CONTRACT.md``. New BSGateway-side
keys (``workspace_dir``, ``mcp_servers``) ride inside ``metadata`` —
``mcp_servers`` is omitted entirely when ``None`` / empty so the worker
preserves pre-E5 behaviour.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)


class BSGatewayError(Exception):
    """Raised when BSGateway returns a terminal error chunk or HTTP error.

    The orchestrator catches this at the executor boundary and
    transitions the run to ``blocked`` — no in-band retry logic here
    (rate-limit / timeout retries belong inside the BSGateway worker).

    ``partial_output`` carries the text streamed before the failure
    happened so the founder still sees what claude produced (the
    Inside panel reads ``run.output_ref.inline``). May be empty if the
    failure occurred before any delta arrived.
    """

    def __init__(self, message: str, *, partial_output: str = "") -> None:
        super().__init__(message)
        self.partial_output = partial_output


class BSGatewayClient:
    """Thin async client for ``POST /api/v1/chat/completions``.

    Single instance is safe to share across runs — ``execute()`` opens
    its own ``httpx.AsyncClient`` per call so concurrent dispatches
    don't interleave streams. Per-call timeout is the BSGateway worker
    timeout (3600s default) plus a small grace.
    """

    DEFAULT_TIMEOUT_SECONDS: float = 3700.0

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: float | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout_seconds or self.DEFAULT_TIMEOUT_SECONDS

    async def execute(
        self,
        *,
        messages: list[dict[str, Any]],
        metadata: dict[str, Any],
        model: str,
        workspace_dir: str | None = None,
        mcp_servers: dict[str, Any] | None = None,
        on_chunk: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """POST chat completion, stream the response, return aggregated output.

        Returns a dict shaped like the pre-2026-05-03 ``LiteLLMOrchestratorAdapter``
        result so ``RunOrchestrator.on_run_completed`` writes the same
        ExecutionRun columns:

        - ``output_type`` — always ``"text"`` for now (multi-deliverable
          tool-call expansion is PR2 territory).
        - ``output_ref`` — the concatenated assistant text.
        - ``actual_cost_cents`` — 0 (BSupervisor reports actual cost via
          BSGateway's ``run.post`` hook; BSNexus no longer guesses).
        - ``finish_reason`` — last ``finish_reason`` from the SSE stream
          (``"stop"`` on success).

        Raises:
            BSGatewayError: on terminal error chunk or HTTP error.
        """
        payload_metadata = dict(metadata)
        if workspace_dir is not None:
            payload_metadata["workspace_dir"] = workspace_dir
        if mcp_servers:
            payload_metadata["mcp_servers"] = mcp_servers

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "metadata": payload_metadata,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        url = f"{self._base_url}/api/v1/chat/completions"

        parts: list[str] = []
        finish_reason: str | None = None

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                async with client.stream("POST", url, headers=headers, json=payload) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if not data or data == "[DONE]":
                            continue
                        try:
                            chunk = json.loads(data)
                        except (json.JSONDecodeError, ValueError):
                            logger.debug("bsgateway_skip_unparseable_chunk", line=data[:120])
                            continue

                        # Terminal error — BSGateway emits a chunk with
                        # ``error.code == "executor_failed"`` instead of
                        # the usual delta. Surface as exception so the
                        # orchestrator transitions the run to blocked.
                        err = chunk.get("error")
                        if err:
                            msg = err.get("message") or "BSGateway executor error"
                            raise BSGatewayError(msg, partial_output="".join(parts))

                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        choice = choices[0]
                        delta = choice.get("delta") or {}
                        text = delta.get("content")
                        if isinstance(text, str) and text:
                            parts.append(text)
                            if on_chunk is not None:
                                await on_chunk(text)
                        if choice.get("finish_reason"):
                            finish_reason = choice["finish_reason"]
        except httpx.HTTPError as exc:
            raise BSGatewayError(f"BSGateway HTTP error: {exc}") from exc

        return {
            "output_type": "text",
            "output_ref": "".join(parts),
            "actual_cost_cents": 0,
            "finish_reason": finish_reason,
        }
