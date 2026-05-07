"""``DirectLLMAdapter`` — direct-LLM path with MCP tool loop.

The BSVibe-optional dispatch path (``executor_type=llm_api``).
Calls the LLM provider directly via litellm and runs an MCP-aware
tool loop client-side: connect to BSNexus's MCP server over
streamable-HTTP, list its tools, translate to OpenAI tool schema,
include in the litellm completion, and dispatch tool calls back via
MCP until the model returns a non-tool response.

Same orchestrator-facing contract as :class:`BSGatewayAdapter` so the
dispatcher can swap them without touching ``run_orchestrator``.

Provider abstraction lives entirely in litellm. Local LLMs (Ollama),
Anthropic, OpenAI, Gemini all go through the same loop — pick the
model identifier (``ollama/llama3``, ``anthropic/claude-3-7-sonnet``,
etc.) and the wire to MCP stays identical.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import structlog
from litellm import acompletion

logger = structlog.get_logger(__name__)


# Mirrors ``BSGatewayAdapter.tools_supported`` — informational; the
# actual tool surface comes from the live MCP server's ``list_tools``
# call. The orchestrator hands ``tools_allowed`` for caller info; we
# don't filter here because MCP is the authoritative tool registry.
_TOOLS_SUPPORTED = ["file_read", "file_write", "file_list", "shell_exec"]

# Cap the inner tool loop. claude with MCP rarely exceeds 5-6 tool
# rounds for M0 tasks; 20 is a generous ceiling that catches runaway
# loops without false positives.
_MAX_TOOL_ROUNDS = 20

# Per-iteration timeout — wraps the streaming acompletion + drain.
# ``llm-tool-loop-hang.md`` documented runs sitting in ``running`` for
# 71+ minutes with the dispatch task parked between iter 1 and iter 2.
# Without this guard the litellm/httpx future never resolves and the
# orchestrator can't transition the run. 600s leaves headroom for slow
# local LLMs on big prompts; production callers can override.
_DEFAULT_ITERATION_TIMEOUT_S = 600.0

# Per-MCP-tool-call timeout. The BSNexus MCP server proxies file_write
# / shell_exec / decision.wait — shell_exec already has its own 180s
# ceiling, so we floor at that. Matches the pattern from the same
# known-issue doc ("shell_exec has its own asyncio.wait_for timeout=180s").
_DEFAULT_TOOL_CALL_TIMEOUT_S = 180.0


class DirectLLMError(Exception):
    """Surface for tool-loop failures.

    Carries optional ``partial_output`` so the orchestrator can
    preserve any text the model produced before the failure (mirrors
    ``BSGatewayError`` from the bsgateway client).
    """

    def __init__(self, message: str, *, partial_output: str = "") -> None:
        super().__init__(message)
        self.partial_output = partial_output


class DirectLLMAdapter:
    """Direct LLM client with MCP tool loop.

    Implements the same surface as :class:`BSGatewayAdapter` so the
    dispatcher branches on ``executor_type`` without orchestrator
    changes downstream.
    """

    tools_supported: list[str] = list(_TOOLS_SUPPORTED)

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        project_id: uuid.UUID,
        run_audit_metadata: dict[str, Any] | None = None,
        workspace_dir: str | None = None,
        mcp_servers: dict[str, Any] | None = None,
        on_chunk: Callable[[str], Awaitable[None]] | None = None,
        max_tool_rounds: int = _MAX_TOOL_ROUNDS,
        api_base: str | None = None,
        iteration_timeout_s: float = _DEFAULT_ITERATION_TIMEOUT_S,
        tool_call_timeout_s: float = _DEFAULT_TOOL_CALL_TIMEOUT_S,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._project_id = project_id
        self._run_audit_metadata: dict[str, Any] = dict(run_audit_metadata) if run_audit_metadata else {}
        self._workspace_dir = workspace_dir
        self._mcp_servers = mcp_servers
        self._on_chunk = on_chunk
        # Optional override for self-hosted / local LLM endpoints
        # (ollama on a Tailscale IP, on-prem vLLM, etc.). When unset,
        # litellm uses the provider's default endpoint.
        self._api_base = api_base or None
        self._max_tool_rounds = max_tool_rounds
        self._iteration_timeout_s = iteration_timeout_s
        self._tool_call_timeout_s = tool_call_timeout_s

    def set_run_audit_metadata(self, metadata: dict[str, Any] | None) -> None:
        self._run_audit_metadata = dict(metadata) if metadata else {}

    def set_workspace_dir(self, workspace_dir: str | None) -> None:
        self._workspace_dir = workspace_dir

    def set_mcp_servers(self, mcp_servers: dict[str, Any] | None) -> None:
        self._mcp_servers = mcp_servers

    def set_on_chunk(self, on_chunk: Callable[[str], Awaitable[None]] | None) -> None:
        self._on_chunk = on_chunk

    async def execute(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        tools_allowed: list[str],
        history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Run the completion + MCP tool loop. Returns the same
        executor-result shape as :class:`BSGatewayAdapter`.
        """
        _ = tools_allowed  # informational; MCP server's list_tools is authoritative

        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        for turn in history or []:
            role = turn.get("role")
            content = turn.get("content")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_prompt})

        async with self._mcp_session() as session:
            openai_tools = await self._fetch_openai_tools(session)
            return await self._tool_loop(messages, session, openai_tools)

    # ── MCP session lifecycle ─────────────────────────────────────────

    @asynccontextmanager
    async def _mcp_session(self):
        """Open an MCP client connection to the first configured server.

        BSNexus emits a single ``mcp_servers`` entry keyed ``"bsnexus"``
        (the run-scoped MCP server). We don't multiplex multiple
        servers at the v1 boundary; multi-server support can ride a
        future PR if external tools land. Yields ``None`` if no MCP
        configured — the loop runs without tools.
        """
        if not self._mcp_servers:
            yield None
            return

        # The dispatcher embeds the run-scoped HMAC token as ``?token=``
        # query param on the URL — streamablehttp_client preserves that.
        # NOTE: import the SDK's ``ClientSession`` from the deeper
        # ``mcp.client.session`` path — ``backend/src/mcp/`` is BSNexus's
        # own MCP server module and shadows the top-level ``mcp`` package
        # in some site-packages layouts (production container). The deep
        # path always resolves to the SDK regardless of import order.
        from mcp.client.session import ClientSession  # noqa: PLC0415
        from mcp.client.streamable_http import streamablehttp_client  # noqa: PLC0415

        first = next(iter(self._mcp_servers.values()))
        url = first.get("url") if isinstance(first, dict) else None
        headers = first.get("headers") if isinstance(first, dict) else None
        if not url:
            yield None
            return

        try:
            async with streamablehttp_client(url, headers=headers or None) as (
                read,
                write,
                _get_session_id,
            ):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session
        except Exception as exc:  # noqa: BLE001 — surface as tool-loop failure
            logger.warning(
                "direct_llm_mcp_connect_failed",
                project_id=str(self._project_id),
                error=str(exc),
            )
            # Fall through to no-tools path — the model still produces
            # text deliverable, just without MCP callbacks.
            yield None

    async def _fetch_openai_tools(self, session: Any | None) -> list[dict[str, Any]] | None:
        if session is None:
            return None
        result = await session.list_tools()
        return [_mcp_tool_to_openai(t) for t in result.tools]

    # ── Tool loop ─────────────────────────────────────────────────────

    async def _tool_loop(
        self,
        messages: list[dict[str, Any]],
        session: Any | None,
        openai_tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """Drive the litellm completion + MCP tool dispatch loop.

        Streams content deltas via ``on_chunk`` (Inside-panel live
        output). Tool-call deltas accumulate silently across the
        stream and dispatch to MCP after each round. Loop exits when
        the model returns a non-tool response (``finish_reason="stop"``)
        or the round cap fires.
        """
        aggregated_text: list[str] = []
        finish_reason: str | None = None

        for round_idx in range(self._max_tool_rounds):
            kwargs: dict[str, Any] = {
                "model": self._model,
                "messages": messages,
                "stream": True,
                "api_key": self._api_key,
            }
            if self._api_base:
                kwargs["api_base"] = self._api_base
            if openai_tools:
                kwargs["tools"] = openai_tools

            logger.info(
                "llm_iteration_start",
                project_id=str(self._project_id),
                round_idx=round_idx,
                model=self._model,
                tools_in_kwargs=bool(openai_tools),
                message_count=len(messages),
            )

            try:
                content_parts, tool_calls, this_finish = await asyncio.wait_for(
                    self._run_iteration(kwargs),
                    timeout=self._iteration_timeout_s,
                )
            except asyncio.TimeoutError as exc:
                logger.warning(
                    "llm_iteration_timeout",
                    project_id=str(self._project_id),
                    round_idx=round_idx,
                    timeout_s=self._iteration_timeout_s,
                )
                raise DirectLLMError(
                    f"llm iteration {round_idx} exceeded {self._iteration_timeout_s}s timeout",
                    partial_output="".join(aggregated_text),
                ) from exc
            except Exception as exc:  # noqa: BLE001
                raise DirectLLMError(
                    f"litellm completion failed: {exc}",
                    partial_output="".join(aggregated_text),
                ) from exc

            logger.info(
                "llm_iteration_returned",
                project_id=str(self._project_id),
                round_idx=round_idx,
                content_chars=sum(len(c) for c in content_parts),
                tool_call_count=len(tool_calls),
                finish_reason=this_finish,
            )

            aggregated_text.extend(content_parts)
            finish_reason = this_finish

            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": "".join(content_parts) or None,
            }
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            messages.append(assistant_msg)

            if not tool_calls or session is None:
                # Done — model returned plain text or there's no MCP
                # session to dispatch tool calls on.
                break

            # Dispatch each tool call via MCP, append the result, loop.
            for call in tool_calls:
                tool_msg = await self._dispatch_tool_call(session, call)
                messages.append(tool_msg)
        else:
            # Round cap hit — surface as error so the orchestrator
            # transitions the run to blocked instead of looping forever.
            raise DirectLLMError(
                f"tool loop exceeded {self._max_tool_rounds} rounds",
                partial_output="".join(aggregated_text),
            )

        # Match the BSGatewayAdapter / dispatcher contract: ``output_ref``
        # is a JSON object, not a bare string. ``ExecutionRunResponse``
        # types it as ``dict[str, Any] | None`` (founder.py:118), and the
        # /api/v1/requests/{id}/runs endpoint 500s on ResponseValidationError
        # if a row carries a string here. Convention is ``{"inline": text}``
        # for inline-text deliverables (see dispatcher.py:218).
        return {
            "output_type": "text",
            "output_ref": {"inline": "".join(aggregated_text)},
            "actual_cost_cents": 0,
            "finish_reason": finish_reason,
        }

    async def _run_iteration(
        self,
        kwargs: dict[str, Any],
    ) -> tuple[list[str], list[dict[str, Any]], str | None]:
        """One ``acompletion`` call + full stream drain.

        Wrapped this way so a single ``asyncio.wait_for`` covers both
        the request and the streaming response. The known-issue doc
        described the hang as "between iter 1 returning and iter 2
        firing" — meaning either the post-stream tool dispatch or the
        next acompletion. Wrapping the *whole* iteration also catches
        a stream that opens fine but parks mid-drain on an httpx
        future that never resolves.
        """
        stream = await acompletion(**kwargs)
        return await self._consume_stream(stream)

    async def _consume_stream(
        self,
        stream: Any,
    ) -> tuple[list[str], list[dict[str, Any]], str | None]:
        """Drain a litellm streaming response. Returns
        ``(content_parts, tool_calls, finish_reason)``.

        ``tool_calls`` deltas come through fragmentary (id +
        function.arguments accumulate across chunks) — we coalesce
        them by index per the OpenAI streaming spec.
        """
        content_parts: list[str] = []
        tool_calls_by_index: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None

        async for chunk in stream:
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            choice = choices[0]
            delta = getattr(choice, "delta", None)
            if delta is None:
                continue

            content = getattr(delta, "content", None)
            if isinstance(content, str) and content:
                content_parts.append(content)
                if self._on_chunk is not None:
                    await self._on_chunk(content)

            tcalls = getattr(delta, "tool_calls", None) or []
            for tc in tcalls:
                idx = getattr(tc, "index", 0) or 0
                slot = tool_calls_by_index.setdefault(
                    idx,
                    {
                        "id": "",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    },
                )
                if getattr(tc, "id", None):
                    slot["id"] = tc.id
                fn = getattr(tc, "function", None)
                if fn is not None:
                    if getattr(fn, "name", None):
                        slot["function"]["name"] = fn.name
                    if getattr(fn, "arguments", None):
                        slot["function"]["arguments"] += fn.arguments

            this_finish = getattr(choice, "finish_reason", None)
            if this_finish:
                finish_reason = this_finish

        tool_calls = [tool_calls_by_index[k] for k in sorted(tool_calls_by_index)]
        return content_parts, tool_calls, finish_reason

    async def _dispatch_tool_call(
        self,
        session: Any,
        call: dict[str, Any],
    ) -> dict[str, Any]:
        """Call the MCP tool, return the OpenAI ``tool``-role message
        to append to the next round's ``messages``.

        Bracketed by ``tool_call_start`` / ``tool_call_done`` events
        and protected by ``_tool_call_timeout_s`` so a hung MCP server
        can't sink the dispatch task. On timeout we surface the
        timeout as a tool-message error string — symmetric with the
        existing exception branch — so the model can recover on the
        next round (``session.call_tool`` succeeded once already, the
        next try might).
        """
        name = call["function"]["name"]
        raw_args = call["function"]["arguments"] or "{}"
        try:
            args = json.loads(raw_args)
        except (ValueError, TypeError):
            args = {}

        logger.info(
            "tool_call_start",
            tool=name,
            project_id=str(self._project_id),
            tool_call_id=call.get("id"),
        )

        try:
            result = await asyncio.wait_for(
                session.call_tool(name, args),
                timeout=self._tool_call_timeout_s,
            )
            content = _serialise_tool_result(result)
            outcome = "ok"
        except asyncio.TimeoutError:
            logger.warning(
                "tool_call_timeout",
                tool=name,
                project_id=str(self._project_id),
                tool_call_id=call.get("id"),
                timeout_s=self._tool_call_timeout_s,
            )
            content = json.dumps({"error": f"tool call timeout after {self._tool_call_timeout_s}s"})
            outcome = "timeout"
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "direct_llm_tool_call_failed",
                tool=name,
                project_id=str(self._project_id),
                error=str(exc),
            )
            content = json.dumps({"error": str(exc)})
            outcome = "error"

        logger.info(
            "tool_call_done",
            tool=name,
            project_id=str(self._project_id),
            tool_call_id=call.get("id"),
            outcome=outcome,
        )

        return {
            "role": "tool",
            "tool_call_id": call["id"],
            "content": content,
        }


# ── MCP ↔ OpenAI translation ──────────────────────────────────────────


def _mcp_tool_to_openai(tool: Any) -> dict[str, Any]:
    """Convert an ``mcp.types.Tool`` into the OpenAI function-tool shape.

    MCP tool: ``{name, description, inputSchema}`` (JSON-Schema for inputs).
    OpenAI tool: ``{type: "function", function: {name, description, parameters}}``.
    """
    schema = getattr(tool, "inputSchema", None)
    if schema is None or not isinstance(schema, dict):
        schema = {"type": "object", "properties": {}}
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": getattr(tool, "description", "") or "",
            "parameters": schema,
        },
    }


def _serialise_tool_result(result: Any) -> str:
    """Flatten an ``mcp.types.CallToolResult`` into the string that
    OpenAI's ``tool``-role message expects.

    MCP returns ``{content: [TextContent | ImageContent | ...], isError}``.
    For text-only tools (the BSNexus surface today) we concatenate the
    text blocks; non-text content gets a placeholder so the model
    knows something landed but can't read it.
    """
    content = getattr(result, "content", None) or []
    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
        else:
            parts.append("[non-text content omitted]")
    return "\n".join(parts) or "[empty]"
