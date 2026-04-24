"""Tools the LLM can call during a run.

The orchestrator's LiteLLM adapter runs a tool-calling loop: the model
may emit ``tool_calls`` on the assistant message; we execute each one
against the project workspace and feed the result back as a ``role:
"tool"`` message. The loop terminates when the assistant replies
without tool calls (or hits the iteration cap).

Three tools exist today — ``file_write``, ``file_read``, ``file_list``.
All operate on the per-project workspace directory managed by
``core.project_workspace``. Nothing here touches the host filesystem
outside that root; ``project_workspace.write_file`` already refuses
paths that escape.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog

from backend.src.core import project_workspace as workspace_store

logger = structlog.get_logger(__name__)


# Cap the LLM's generated output for a single file. GLM / llama-class
# models occasionally emit runaway repetition; truncating at ~1 MB
# keeps a bad run from blowing up the workspace. Real files are
# typically well under 64 KB.
MAX_FILE_BYTES = 1_000_000

# shell_exec safety caps.
SHELL_DEFAULT_TIMEOUT_S = 180
SHELL_MAX_TIMEOUT_S = 900
SHELL_MAX_OUTPUT_BYTES = 20_000


@dataclass
class WrittenFile:
    """Audit trail entry for one successful ``file_write`` call."""

    path: str
    size: int
    language: str = ""

    def to_ref(self) -> dict[str, Any]:
        return {"path": self.path, "language": self.language, "size": self.size}


@dataclass
class ShellInvocation:
    """Record of one ``shell_exec`` call (command + outcome)."""

    command: str
    exit_code: int
    duration_ms: int


@dataclass
class ToolRunLog:
    """Accumulated tool-call outcomes for a single run.

    The adapter passes a fresh ``ToolRunLog`` into each ``execute`` call
    and reads it back to populate ``output_ref.files`` etc.
    """

    project_id: uuid.UUID
    written: list[WrittenFile] = field(default_factory=list)
    shells: list[ShellInvocation] = field(default_factory=list)
    invocations: int = 0
    errors: int = 0

    def record_write(self, path: str, content: str, language: str = "") -> None:
        self.written.append(
            WrittenFile(path=path, size=len(content.encode("utf-8")), language=language)
        )

    def record_shell(self, command: str, exit_code: int, duration_ms: int) -> None:
        self.shells.append(
            ShellInvocation(command=command[:240], exit_code=exit_code, duration_ms=duration_ms)
        )


def tool_schemas(allowed: list[str] | None = None) -> list[dict[str, Any]]:
    """Return OpenAI-style function schemas for the subset that's allowed.

    ``allowed`` matches tool names (``file_write`` / ``file_read`` /
    ``file_list`` / ``shell_exec``). Passing ``None`` or an empty list
    returns every tool — convenient for the default case where the
    composer already told the worker which tools exist.
    """
    all_schemas = [
        _FILE_WRITE_SCHEMA,
        _FILE_READ_SCHEMA,
        _FILE_LIST_SCHEMA,
        _SHELL_EXEC_SCHEMA,
    ]
    if not allowed:
        return all_schemas
    allow_set = set(allowed)
    return [s for s in all_schemas if s["function"]["name"] in allow_set]


async def execute_tool_call(
    *,
    name: str,
    raw_arguments: str,
    log: ToolRunLog,
) -> str:
    """Run one tool call; return the stringified result to feed back to the LLM.

    Errors are returned to the model as plain-text error payloads so it
    can correct course (e.g. retry with a different path). We never
    raise out — a tool error should not abort the whole run.
    """
    log.invocations += 1
    try:
        arguments = json.loads(raw_arguments) if raw_arguments else {}
    except json.JSONDecodeError as exc:
        log.errors += 1
        logger.warning("tool_arguments_invalid_json", name=name, error=str(exc))
        return f"error: invalid JSON arguments ({exc})"

    if not isinstance(arguments, dict):
        log.errors += 1
        return "error: arguments must be a JSON object"

    try:
        if name == "file_write":
            return await _handle_file_write(arguments, log)
        if name == "file_read":
            return await _handle_file_read(arguments, log)
        if name == "file_list":
            return await _handle_file_list(arguments, log)
        if name == "shell_exec":
            return await _handle_shell_exec(arguments, log)
    except ValueError as exc:
        log.errors += 1
        logger.info("tool_rejected", name=name, error=str(exc))
        return f"error: {exc}"
    except Exception as exc:  # noqa: BLE001 — tool errors must not kill the loop
        log.errors += 1
        logger.exception("tool_unexpected_error", name=name)
        return f"error: unexpected failure ({exc})"

    log.errors += 1
    return f"error: unknown tool {name!r}"


# ────────────────────────── handlers ──────────────────────────


async def _handle_file_write(args: dict[str, Any], log: ToolRunLog) -> str:
    path = _str_arg(args, "path")
    content = _str_arg(args, "content", allow_empty=True)
    language = str(args.get("language") or "").strip()
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        raise ValueError(
            f"file content exceeds {MAX_FILE_BYTES} bytes; split into smaller files"
        )
    target = workspace_store.write_file(log.project_id, path, content)
    log.record_write(path=path, content=content, language=language)
    logger.info(
        "tool_file_write",
        project_id=str(log.project_id),
        path=path,
        bytes=len(content.encode("utf-8")),
    )
    return f"ok: wrote {path} ({target.stat().st_size} bytes)"


async def _handle_file_read(args: dict[str, Any], log: ToolRunLog) -> str:
    path = _str_arg(args, "path")
    content = workspace_store.read_file(log.project_id, path)
    if content is None:
        return f"error: no such file {path!r}"
    return content


async def _handle_file_list(args: dict[str, Any], log: ToolRunLog) -> str:
    # ``args`` currently unused — the tool just lists everything. Future
    # versions might accept a prefix filter.
    _ = args
    entries = workspace_store.list_files(log.project_id)
    if not entries:
        return "(workspace is empty)"
    return "\n".join(f"{e['path']}\t{e['size']}" for e in entries)


async def _handle_shell_exec(args: dict[str, Any], log: ToolRunLog) -> str:
    command = _str_arg(args, "command")
    timeout_raw = args.get("timeout_s")
    try:
        timeout = int(timeout_raw) if timeout_raw is not None else SHELL_DEFAULT_TIMEOUT_S
    except (TypeError, ValueError):
        timeout = SHELL_DEFAULT_TIMEOUT_S
    timeout = max(1, min(timeout, SHELL_MAX_TIMEOUT_S))

    cwd = workspace_store.project_workspace_path(log.project_id)

    start_ns = asyncio.get_event_loop().time()
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, "CI": "1"},  # tell tools like pnpm not to interact
        )
    except Exception as exc:  # noqa: BLE001
        log.record_shell(command=command, exit_code=-1, duration_ms=0)
        return f"error: shell spawn failed ({exc})"

    timed_out = False
    try:
        stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        timed_out = True
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await proc.wait()
        except Exception:  # noqa: BLE001
            pass
        stdout_bytes = b""

    duration_ms = int((asyncio.get_event_loop().time() - start_ns) * 1000)
    output = (stdout_bytes or b"").decode("utf-8", errors="replace")
    if len(output.encode("utf-8")) > SHELL_MAX_OUTPUT_BYTES:
        clipped = output.encode("utf-8")[:SHELL_MAX_OUTPUT_BYTES].decode("utf-8", errors="ignore")
        output = clipped + f"\n[... truncated at {SHELL_MAX_OUTPUT_BYTES} bytes ...]"

    exit_code = -1 if timed_out else int(proc.returncode or 0)
    log.record_shell(command=command, exit_code=exit_code, duration_ms=duration_ms)
    logger.info(
        "tool_shell_exec",
        project_id=str(log.project_id),
        cmd_preview=command[:80],
        exit_code=exit_code,
        duration_ms=duration_ms,
        timed_out=timed_out,
    )
    if timed_out:
        return (
            f"exit=-1 (timeout after {timeout}s)\n"
            f"{output}"
        )
    return f"exit={exit_code}\n{output}"


def _str_arg(args: dict[str, Any], key: str, *, allow_empty: bool = False) -> str:
    value = args.get(key)
    if value is None:
        raise ValueError(f"missing required argument {key!r}")
    if not isinstance(value, str):
        raise ValueError(f"argument {key!r} must be a string, got {type(value).__name__}")
    if not allow_empty and not value.strip():
        raise ValueError(f"argument {key!r} must not be empty")
    return value


# ────────────────────────── schemas ──────────────────────────

_FILE_WRITE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "file_write",
        "description": (
            "Create or overwrite a file in the project workspace. Use "
            "this for every artifact (code, docs, configs, designs, data) "
            "the founder expects to receive — the chat reply is NOT "
            "persisted as a file. Paths are relative to the workspace "
            "root; subdirectories are created automatically."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Workspace-relative path, e.g. 'backend/main.py'.",
                },
                "content": {
                    "type": "string",
                    "description": "Full file contents. Write complete, runnable content — no placeholders.",
                },
                "language": {
                    "type": "string",
                    "description": "Optional language/format hint (python, typescript, markdown, …) used when indexing the deliverable.",
                },
            },
            "required": ["path", "content"],
        },
    },
}


_FILE_READ_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "file_read",
        "description": (
            "Read an existing file from the project workspace. Use this "
            "before overwriting a file another phase already produced, "
            "so you preserve prior work."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Workspace-relative path to read.",
                },
            },
            "required": ["path"],
        },
    },
}


_FILE_LIST_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "file_list",
        "description": (
            "List every file already in the project workspace "
            "(path\\tbytes). Handy before a phase writes files so you "
            "can see what's there and integrate with it."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


_SHELL_EXEC_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "shell_exec",
        "description": (
            "Run a shell command inside the project workspace. Use this "
            "to VERIFY your work — install deps, run the build, execute "
            "a test, curl a started server, parse a JSON file with "
            "python -m json.tool, etc. The command runs with the "
            "workspace as cwd; you cannot escape it via relative paths. "
            "Stdout+stderr are returned merged, with 'exit=<code>' on "
            "the first line. Do NOT mark a phase done until a verifying "
            "shell_exec returns exit=0."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to run. Supports pipes, &&, etc.",
                },
                "timeout_s": {
                    "type": "integer",
                    "description": f"Max seconds to wait (default {SHELL_DEFAULT_TIMEOUT_S}, max {SHELL_MAX_TIMEOUT_S}).",
                },
            },
            "required": ["command"],
        },
    },
}
