"""Shell execution tool.

Without a way to actually run the code they produce, agents have no
feedback signal beyond "I wrote some files" — which session-10 longruns
repeatedly showed is a recipe for confident hallucination. This tool
gives passive-mode workers a minimal, sandbox-scoped exec primitive so
they can run build/test/smoke commands and read real stdout/exit codes
back into their own reasoning.

Scope is intentionally small: one command at a time, workspace as cwd,
hard timeout, bounded stdout/stderr. Anything beyond is the execution-
infra session's concern.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import structlog

from backend.src.tools.base import Tool, ToolContext, ToolExecutionError

logger = structlog.get_logger(__name__)


_DEFAULT_TIMEOUT_S = 60
_MAX_TIMEOUT_S = 300
_STREAM_BYTE_LIMIT = 10_000  # per stream, per call


def _effective_timeout(requested: Any) -> int:
    """Clamp the agent-supplied timeout into a sane range."""
    try:
        n = int(requested)
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT_S
    if n <= 0:
        return _DEFAULT_TIMEOUT_S
    return min(n, _MAX_TIMEOUT_S)


def _truncate(buf: bytes) -> tuple[str, bool]:
    """Return (decoded, truncated_flag)."""
    if len(buf) <= _STREAM_BYTE_LIMIT:
        return buf.decode("utf-8", errors="replace"), False
    head = buf[:_STREAM_BYTE_LIMIT]
    return head.decode("utf-8", errors="replace") + "\n…[truncated]", True


class ShellExecTool(Tool):
    """Run a shell command inside the project workspace."""

    @property
    def name(self) -> str:
        return "shell_exec"

    @property
    def description(self) -> str:
        return (
            "Run a shell command inside the project workspace and capture "
            "stdout, stderr, exit code, and duration. Use this to actually "
            "verify your work — build, test, lint, smoke-run the code or "
            "docs you produced. Do NOT claim a task is done without a "
            "successful run here when a run is possible. Default timeout "
            "60s (max 300s). Each stream is capped at ~10KB; anything "
            "longer is truncated with a marker."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": (
                        "Shell command to run (passed to /bin/sh -c). "
                        "Example: 'npm install && npm test' or "
                        "'node --check src/app.js'. The cwd is the "
                        "project workspace root."
                    ),
                },
                "timeout_s": {
                    "type": "integer",
                    "description": (
                        "Kill the command after this many seconds. "
                        f"Default {_DEFAULT_TIMEOUT_S}, clamped to "
                        f"[1, {_MAX_TIMEOUT_S}]."
                    ),
                },
            },
            "required": ["command"],
        }

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str:
        command = (input.get("command") or "").strip()
        if not command:
            raise ToolExecutionError(
                "shell_exec: 'command' is required and must not be empty."
            )

        timeout_s = _effective_timeout(input.get("timeout_s"))

        ctx.workspace_path.mkdir(parents=True, exist_ok=True)

        started = time.monotonic()
        timed_out = False
        proc: asyncio.subprocess.Process | None = None
        stdout_bytes = b""
        stderr_bytes = b""

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(ctx.workspace_path),
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout_s,
                )
            except asyncio.TimeoutError:
                timed_out = True
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                # Drain whatever was buffered so we still return evidence.
                try:
                    stdout_bytes, stderr_bytes = await proc.communicate()
                except Exception:
                    pass
        except Exception as exc:
            raise ToolExecutionError(f"shell_exec: failed to launch command: {exc}") from exc

        duration_s = round(time.monotonic() - started, 3)
        exit_code = proc.returncode if proc is not None else -1
        if timed_out and (exit_code is None or exit_code == 0):
            # Some shells exit 0 on SIGKILL race; make failure visible.
            exit_code = -9

        stdout_text, stdout_trunc = _truncate(stdout_bytes or b"")
        stderr_text, stderr_trunc = _truncate(stderr_bytes or b"")

        logger.info(
            "shell_exec_ran",
            agent=ctx.agent_name,
            command=command[:200],
            exit_code=exit_code,
            duration_s=duration_s,
            timed_out=timed_out,
            stdout_bytes=len(stdout_bytes or b""),
            stderr_bytes=len(stderr_bytes or b""),
        )

        return json.dumps({
            "command": command,
            "cwd": str(ctx.workspace_path),
            "exit_code": exit_code,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "duration_s": duration_s,
            "timed_out": timed_out,
            "truncated": bool(stdout_trunc or stderr_trunc),
        })
