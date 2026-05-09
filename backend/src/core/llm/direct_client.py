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
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import structlog
from litellm import acompletion

logger = structlog.get_logger(__name__)


# PR10 — built-in tool names dispatched LOCALLY (not via MCP). The
# names match ``backend.src.core.tools`` handlers. MCP server's
# domain tools (decision_create, knowledge_search, etc.) take
# whatever names the MCP server registers — handled by the else
# branch in ``_dispatch_tool_call``.
_LOCAL_TOOL_NAMES: frozenset[str] = frozenset({"file_write", "file_read", "file_list", "shell_exec"})


# Hard cap on the size of ``args`` we copy into the activity log.
# file_write payloads can run several KB; we keep a 1KB excerpt for
# debug context but don't blow the JSONB cell or duplicate the file
# content (workspace is the source of truth for the actual write).
_TOOL_ACTIVITY_ARGS_CAP = 1024

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

# Per-iteration generation budget in tokens. Round 1 (2026-05-07)
# revealed default litellm/Ollama caps truncating most outputs at
# ~150-500 chars mid-response (M0 task corpus had 6/10 tasks visibly
# truncated). 4096 covers single-file artifacts comfortably; longer
# multi-file builds chain across iterations via MCP file_write so
# the per-iteration budget stays modest.
_DEFAULT_MAX_TOKENS = 4096


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

    # Class-level default; overridden by tests / orchestrator. Live
    # tool surface comes from the MCP server's ``list_tools`` call.
    tools_supported: list[str] = ["file_read", "file_write", "file_list", "shell_exec"]

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
        max_tokens: int = _DEFAULT_MAX_TOKENS,
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
        self._max_tokens = max_tokens
        # In-memory activity log accumulated during ``execute()`` and
        # returned in the result dict so the dispatcher can persist
        # rows after the LLM call finishes (PR7). DB session is NOT
        # held during ``execute()`` — see dispatcher.py Phase 2 / 3.
        self._tool_activity_log: list[dict[str, Any]] = []
        # PR10 — local-tool log (filesystem writes + shell invocations)
        # accumulated during ``execute()``. Lazy-default so callers
        # that hit ``_tool_loop`` directly (tests) don't AttributeError;
        # ``execute()`` overwrites with a fresh per-run instance.
        from backend.src.core.tools import ToolRunLog as _ToolRunLog  # noqa: PLC0415

        self._local_tool_log = _ToolRunLog(project_id=project_id)

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
        """Run the completion + tool loop. Returns the same
        executor-result shape as :class:`BSGatewayAdapter`, plus a
        ``tool_activity_log`` list the dispatcher persists as
        ``ExecutionRunActivity`` rows after the LLM call finishes
        AND a ``local_tool_log`` summary (file writes + shell
        invocations) so ``publish_run_output`` can auto-derive the
        deliverable's verification block.
        """
        _ = tools_allowed  # informational; tool surface union below is authoritative

        # PR10 — local-tool log (filesystem writes + shell invocations).
        # ``core.tools.execute_tool_call`` writes into this; the result
        # exposes it so the dispatcher can stamp the verification
        # block from observed shell_exec history.
        from backend.src.core.tools import ToolRunLog  # noqa: PLC0415

        self._local_tool_log = ToolRunLog(project_id=self._project_id)

        # Fresh log per execute() — supports adapter reuse across runs.
        self._tool_activity_log = []

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
            # ExceptionGroup (Python 3.11) wraps the real cause inside
            # ``.exceptions``; ``str(exc)`` alone gives only the wrapper
            # text "unhandled errors in a TaskGroup (1 sub-exception)".
            # Walk the group so the warning carries the actual cause.
            sub_excs = getattr(exc, "exceptions", None)
            if sub_excs:
                inner = sub_excs[0]
                error_repr = f"{type(inner).__name__}: {inner}"
            else:
                error_repr = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "direct_llm_mcp_connect_failed",
                project_id=str(self._project_id),
                url=url,
                error=error_repr,
                exc_info=True,
            )
            # Fall through to no-tools path — the model still produces
            # text deliverable, just without MCP callbacks.
            yield None

    async def _fetch_openai_tools(self, session: Any | None) -> list[dict[str, Any]] | None:
        """Union the local built-in tool schemas with the MCP server's
        domain tools and present one list to the LLM. Local tools
        (file_write / file_read / file_list / shell_exec) execute
        in-process; MCP tools (decision_create, knowledge_search,
        etc.) round-trip to the configured server. The LLM doesn't
        need to know which is which — it just sees one tool surface.
        """
        from backend.src.core.tools import tool_schemas as local_tool_schemas  # noqa: PLC0415

        local = local_tool_schemas()
        if session is None:
            return local or None
        result = await session.list_tools()
        mcp = [_mcp_tool_to_openai(t) for t in result.tools]
        return local + mcp

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
        per_round_replies: list[str] = []
        finish_reason: str | None = None

        for round_idx in range(self._max_tool_rounds):
            kwargs: dict[str, Any] = {
                "model": self._model,
                "messages": messages,
                "stream": True,
                "api_key": self._api_key,
                "max_tokens": self._max_tokens,
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

            round_reply = "".join(content_parts)
            content_chars = len(round_reply)
            tool_call_count = len(tool_calls)
            logger.info(
                "llm_iteration_returned",
                project_id=str(self._project_id),
                round_idx=round_idx,
                content_chars=content_chars,
                tool_call_count=tool_call_count,
                finish_reason=this_finish,
            )
            self._tool_activity_log.append(
                {
                    "kind": "llm_round_complete",
                    "round_idx": round_idx,
                    "content_chars": content_chars,
                    "tool_call_count": tool_call_count,
                    "finish_reason": this_finish,
                    "occurred_at": datetime.now(timezone.utc).isoformat(),
                }
            )

            per_round_replies.append(round_reply)
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
                # session to dispatch tool calls on. PR10 dropped the
                # PR9 watchdog: verification block is auto-derived
                # from the local-tool log's shell_exec history rather
                # than coerced out of the LLM via re-nudges.
                break

            # Dispatch each tool call (local or MCP), append result, loop.
            for call in tool_calls:
                tool_msg = await self._dispatch_tool_call(session, call, round_idx=round_idx)
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
        # /api/v1/runs?request_id={id} endpoint 500s on ResponseValidationError
        # if a row carries a string here. Convention is ``{"inline": text}``
        # for inline-text deliverables (see dispatcher.py:218).
        return {
            "output_type": "text",
            "output_ref": {"inline": "".join(aggregated_text)},
            "actual_cost_cents": 0,
            "finish_reason": finish_reason,
            "tool_activity_log": list(self._tool_activity_log),
            "per_round_replies": list(per_round_replies),
            "local_tool_log": {
                "written_files": [w.to_ref() for w in self._local_tool_log.written],
                "shell_invocations": [
                    {"command": s.command, "exit_code": s.exit_code, "duration_ms": s.duration_ms}
                    for s in self._local_tool_log.shells
                ],
            },
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
        tool_calls = _split_concatenated_tool_call_arguments(tool_calls)
        return content_parts, tool_calls, finish_reason

    async def _dispatch_tool_call(
        self,
        session: Any,
        call: dict[str, Any],
        *,
        round_idx: int = 0,
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
        original_name = call["function"]["name"]
        raw_args = call["function"]["arguments"] or "{}"
        try:
            args = json.loads(raw_args)
        except (ValueError, TypeError):
            args = {}

        # PR11 — qwen3-coder dogfood iter 3 showed weak coder models
        # consolidate to one tool name (typically ``shell_exec``) and
        # emit file_write payloads under it. Recover the intent from
        # unambiguous arg shape BEFORE routing. ``execute_tool_call``
        # also recovers internally; doing it here too keeps the
        # local/MCP routing decision honest if the model misnames in a
        # way that crosses the boundary.
        recovery_note: str | None = None
        if isinstance(args, dict):
            from backend.src.core.tools import recover_misnamed_local_tool  # noqa: PLC0415

            recovered_name, recovery_note = recover_misnamed_local_tool(original_name, args)
            name = recovered_name
        else:
            name = original_name

        tool_call_id = call.get("id")
        args_excerpt = raw_args[:_TOOL_ACTIVITY_ARGS_CAP]
        started_at = datetime.now(timezone.utc)
        started_perf = time.perf_counter()

        logger.info(
            "tool_call_start",
            tool=name,
            project_id=str(self._project_id),
            tool_call_id=tool_call_id,
            round_idx=round_idx,
            recovered_from=original_name if recovery_note else None,
        )
        activity_entry: dict[str, Any] = {
            "kind": "tool_call_start",
            "round_idx": round_idx,
            "tool_name": name,
            "tool_call_id": tool_call_id,
            "args": args_excerpt,
            "occurred_at": started_at.isoformat(),
        }
        if recovery_note:
            activity_entry["recovered_from"] = original_name
            activity_entry["recovery_note"] = recovery_note
        self._tool_activity_log.append(activity_entry)

        error_message: str | None = None
        try:
            if name in _LOCAL_TOOL_NAMES:
                # PR10 — built-in tools (file_write / file_read /
                # file_list / shell_exec) execute LOCALLY against the
                # project workspace. Mirrors claude-code's architecture:
                # filesystem / shell are local; MCP is for backend
                # domain extension only.
                from backend.src.core.tools import execute_tool_call  # noqa: PLC0415

                content = await asyncio.wait_for(
                    execute_tool_call(
                        name=name,
                        raw_arguments=raw_args,
                        log=self._local_tool_log,
                    ),
                    timeout=self._tool_call_timeout_s,
                )
                # Local handler returns "error: ..." strings; keep
                # them as the tool message but flag outcome=error so
                # the activity log distinguishes them.
                outcome = "error" if content.startswith("error:") else "ok"
            else:
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
                tool_call_id=tool_call_id,
                timeout_s=self._tool_call_timeout_s,
            )
            content = json.dumps({"error": f"tool call timeout after {self._tool_call_timeout_s}s"})
            outcome = "timeout"
            error_message = f"timeout after {self._tool_call_timeout_s}s"
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "direct_llm_tool_call_failed",
                tool=name,
                project_id=str(self._project_id),
                error=str(exc),
            )
            content = json.dumps({"error": str(exc)})
            outcome = "error"
            error_message = str(exc)

        duration_ms = int((time.perf_counter() - started_perf) * 1000)
        finished_at = datetime.now(timezone.utc)
        logger.info(
            "tool_call_done",
            tool=name,
            project_id=str(self._project_id),
            tool_call_id=tool_call_id,
            outcome=outcome,
            duration_ms=duration_ms,
            round_idx=round_idx,
        )
        done_record: dict[str, Any] = {
            "kind": "tool_call_done",
            "round_idx": round_idx,
            "tool_name": name,
            "tool_call_id": tool_call_id,
            "outcome": outcome,
            "duration_ms": duration_ms,
            "occurred_at": finished_at.isoformat(),
        }
        if error_message is not None:
            done_record["error"] = error_message
        self._tool_activity_log.append(done_record)

        return {
            "role": "tool",
            "tool_call_id": call["id"],
            "content": content,
        }


def _split_concatenated_tool_call_arguments(
    tool_calls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Split an accumulated tool_call slot whose ``arguments`` string
    parses as multiple back-to-back JSON objects into one tool_call per
    object.

    Backstop for an Ollama streaming quirk surfaced by the live-LLM
    e2e dogfood pass on 2026-05-08: ``ollama_chat/qwen3-coder:30b``
    emitted two ``file_write`` tool_calls in a single response chunk
    using the same streaming index (per the OpenAI streaming spec that
    convention is "two distinct calls under different indices"); our
    per-index accumulator concatenated their argument strings into
    ``{"path": "a.py", ...}{"path": "tests/b.py", ...}``. Litellm's
    ollama transform later raised ``JSONDecodeError: Extra data`` on
    the next round, blocking the run with no recovery.

    Reproduce: send a real-LLM request whose system prompt asks for two
    files in one turn — pre-fix the run transitions to ``blocked`` on
    round 1 with the litellm transform error visible in the uvicorn
    log.
    """
    if not tool_calls:
        return tool_calls

    decoder = json.JSONDecoder()
    expanded: list[dict[str, Any]] = []
    for tc in tool_calls:
        args = tc.get("function", {}).get("arguments") or ""
        if not args:
            expanded.append(tc)
            continue
        try:
            json.loads(args)
            expanded.append(tc)
            continue
        except json.JSONDecodeError:
            pass

        # Walk via raw_decode and emit one tool_call per parsed object.
        cursor = 0
        seq = 0
        produced_any = False
        text_len = len(args)
        while cursor < text_len:
            # Skip whitespace between JSON objects.
            while cursor < text_len and args[cursor].isspace():
                cursor += 1
            if cursor >= text_len:
                break
            try:
                obj, end = decoder.raw_decode(args, cursor)
            except json.JSONDecodeError:
                logger.warning(
                    "tool_call_argument_split_remainder_dropped",
                    tool_call_id=tc.get("id"),
                    remaining=args[cursor : cursor + 80],
                )
                break
            sub = dict(tc)
            sub["function"] = dict(tc["function"])
            sub["function"]["arguments"] = json.dumps(obj)
            if seq > 0 and tc.get("id"):
                # Suffix follow-on calls so each tool_message in the
                # next round can address the right call_id.
                sub["id"] = f"{tc['id']}-{seq}"
            expanded.append(sub)
            cursor = end
            seq += 1
            produced_any = True

        if not produced_any:
            # Couldn't recover anything — keep the original; the
            # downstream tool dispatch will still fail cleanly with a
            # JSON-parse error message we surface to the model.
            expanded.append(tc)

    return expanded


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
