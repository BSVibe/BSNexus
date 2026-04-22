"""Adapter that exposes ClaudeCodeExecutor under the LiteLLMExecutor
interface (messages / tools / tool_handler / ExecutionResult with
tool_calls_made).

Context (#39): agent_chat used to hardcode LiteLLMExecutor(), which
silently ignored any ``executor_configs.executor_type`` other than
``generic_llm``. The factory now dispatches by type, and this adapter
lets ClaudeCodeExecutor plug into the same call site without
rewriting the agent-chat pipeline.

Scope (session 10): flatten messages to a single prompt, invoke the
Claude CLI subprocess, wrap stdout in the LiteLLM-shaped
ExecutionResult. The CLI has its own builtin tools (bash, file edit,
…) so BSNexus ``shell_exec`` is not bridged — verification commands
that Claude runs internally happen inside the CLI and leave real
artifacts on the workspace. ``tool_calls_made`` from BSNexus's
perspective will therefore stay empty; that is the correct wire
shape, not a missing feature.

A full unified executor interface (messages + BSNexus tool_handler
over arbitrary executors) is still #39's ongoing work.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import structlog

from backend.src.core.executor.claude_code import ClaudeCodeExecutor
from backend.src.core.executor.litellm_executor import ExecutionEvent, ExecutionResult
from backend.src.tools.base import ToolDefinition
from backend.src.tools.handler import ToolHandler

logger = structlog.get_logger(__name__)


def _flatten_messages(messages: list[dict[str, Any]]) -> str:
    """Collapse a chat-completion message list into a single prompt.

    Claude CLI takes a single stdin prompt; there is no multi-role
    message channel. Keep system/user/assistant labels explicit so
    the model can still tell speakers apart.
    """
    lines: list[str] = []
    for m in messages:
        role = (m.get("role") or "user").upper()
        content = m.get("content") or ""
        if not isinstance(content, str):
            # Some callers send structured content blocks; serialize safely.
            content = str(content)
        lines.append(f"[{role}]\n{content}")
    return "\n\n".join(lines)


def _tools_hint(tools: list[ToolDefinition] | None) -> str:
    """Short description of available BSNexus tools appended to the prompt.

    The CLI cannot actually invoke BSNexus tools (it has its own built-in
    ones), but surfacing the tool list encourages Claude to emit the
    matching inline markers (``[CREATE_TASK …]``, ``[COMPLETE_TASK …]``)
    which the backend already parses separately.
    """
    if not tools:
        return ""
    names = ", ".join(t.name for t in tools)
    return (
        "\n\n[BSNEXUS_TOOLS_NOTE]\n"
        "The surrounding system can parse inline markers. Available tool "
        f"names you may reference: {names}. For verification, run real "
        "shell commands (npm test, tsc --noEmit, curl, …) directly — the "
        "Claude CLI can execute them."
    )


class ClaudeCodeLLMAdapter:
    """LiteLLMExecutor-shaped wrapper around ClaudeCodeExecutor."""

    def __init__(
        self,
        cli: ClaudeCodeExecutor | None = None,
        workspace_dir: str | None = None,
    ) -> None:
        self._cli = cli or ClaudeCodeExecutor(workspace_dir=workspace_dir)
        self._workspace_dir = workspace_dir

    async def execute(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition] | None,
        tool_handler: ToolHandler | None,
        *,
        model: str = "claude_code",
        api_key: str = "unused",
        base_url: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        max_iterations: int = 10,
        project_id: Any | None = None,
        on_event: Callable[[ExecutionEvent], Any] | None = None,
    ) -> ExecutionResult:
        """Flatten messages → claude CLI → LiteLLM-shaped ExecutionResult."""
        prompt = _flatten_messages(messages) + _tools_hint(tools)

        context: dict[str, Any] = {
            "task_id": str(project_id) if project_id else "agent_chat",
            "workspace_dir": self._workspace_dir,
        }

        # Surface a "start" event so SSE consumers see the run kicked off
        # even if the CLI produces no mid-stream updates.
        if on_event is not None:
            try:
                on_event(ExecutionEvent(type="status_update",
                                        data={"stage": "claude_cli_started"}))
            except Exception:
                pass

        cli_result = await self._cli.execute(prompt, context)

        if not cli_result.success:
            logger.warning(
                "claude_code_adapter_cli_failed",
                error=cli_result.error_message,
                category=cli_result.error_category,
            )
            if on_event is not None:
                try:
                    on_event(ExecutionEvent(type="done",
                                            data={"stop_reason": "error"}))
                except Exception:
                    pass
            return ExecutionResult(
                content="",
                tool_calls_made=[],
                tool_results=[],
                prompt_tokens=0, completion_tokens=0, total_tokens=0,
                cost_usd=0.0,
                model=model,
                stop_reason="error",
                iterations=1,
            )

        content = (cli_result.stdout or "").strip()
        if on_event is not None:
            try:
                on_event(ExecutionEvent(type="done",
                                        data={"stop_reason": "end_turn"}))
            except Exception:
                pass

        return ExecutionResult(
            content=content,
            tool_calls_made=[],  # CLI's internal tool use is opaque to us
            tool_results=[],
            prompt_tokens=0, completion_tokens=0, total_tokens=0,
            cost_usd=0.0,
            model=model,
            stop_reason="end_turn",
            iterations=1,
        )
