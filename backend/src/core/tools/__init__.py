"""G6.6 — workspace-scoped tool registry for the dispatcher tool loop.

Four MVP handlers:
  - ``file_read(path)`` — read text file under workspace_dir
  - ``file_list(path)``  — list directory entries under workspace_dir
  - ``file_write(path, content)`` — create/overwrite file under workspace_dir
  - ``shell_exec(command)`` — run a shell command with cwd=workspace_dir,
    30s timeout, denylist of destructive/network patterns

Every handler refuses to step outside ``workspace_dir`` via path
traversal (``..``, absolute paths). Per CLAUDE.md "Async everywhere",
all I/O is async (asyncio.create_subprocess_exec for shell_exec,
sync filesystem I/O wrapped in ``asyncio.to_thread``).

The registry returns OpenAI-style ``tools=[...]`` JSON schemas that
``LlmClient.complete(tools=...)`` forwards directly to LiteLLM.
"""

from __future__ import annotations

import asyncio
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


SHELL_DENYLIST_PATTERNS: tuple[str, ...] = (
    "rm -rf",
    "rm -fr",
    "rm -r ",
    " rm /",
    "sudo ",
    "curl ",
    "wget ",
    "ssh ",
    "scp ",
    "nc ",
    "ncat ",
    "telnet ",
    ":(){",
    "mkfs",
    "dd ",
    "/dev/sd",
    " > /dev/",
    "shutdown",
    "reboot",
    "halt",
    "kill -9 -1",
    "chmod 777 /",
)
SHELL_TIMEOUT_S: float = 30.0
FILE_READ_MAX_BYTES: int = 256 * 1024
FILE_WRITE_MAX_BYTES: int = 256 * 1024


class ToolError(Exception):
    """Raised when a tool refuses to run (path escape, denylist hit,
    timeout, size cap). The dispatcher surfaces the message back to the
    LLM as the tool result so it can recover."""


@dataclass(frozen=True)
class ToolDefinition:
    """Schema + handler for one tool."""

    name: str
    description: str
    parameters_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[str]]


class ToolRegistry:
    """Workspace-scoped tool registry. One instance per RunAttempt —
    holds the workspace root and stateful denylist enforcement.
    """

    def __init__(self, *, workspace_dir: Path) -> None:
        self._root = workspace_dir.resolve()
        self._tools: dict[str, ToolDefinition] = {}
        self._register_defaults()

    def schema_for(self, names: list[str]) -> list[dict[str, Any]]:
        """Return OpenAI-style ``tools=[...]`` JSON for the given
        tool names — phase gating decides the slice."""
        result: list[dict[str, Any]] = []
        for name in names:
            tool = self._tools.get(name)
            if tool is None:
                continue
            result.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters_schema,
                    },
                }
            )
        return result

    async def invoke(self, name: str, arguments: dict[str, Any]) -> str:
        """Run a tool by name. Returns the result string the dispatcher
        appends as the tool message."""
        tool = self._tools.get(name)
        if tool is None:
            raise ToolError(f"Unknown tool: {name!r}")
        return await tool.handler(arguments)

    def has(self, name: str) -> bool:
        return name in self._tools

    def _register_defaults(self) -> None:
        self._tools["file_read"] = ToolDefinition(
            name="file_read",
            description="Read a UTF-8 text file inside the workspace.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path relative to the workspace root.",
                    },
                },
                "required": ["path"],
            },
            handler=self._file_read,
        )
        self._tools["file_list"] = ToolDefinition(
            name="file_list",
            description="List files and directories under a workspace path.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path relative to the workspace root (default: '.').",
                        "default": ".",
                    },
                },
            },
            handler=self._file_list,
        )
        self._tools["file_write"] = ToolDefinition(
            name="file_write",
            description=(
                "Create or overwrite a UTF-8 text file inside the workspace. Parent directories are created as needed."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path relative to the workspace root.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full file contents to write.",
                    },
                },
                "required": ["path", "content"],
            },
            handler=self._file_write,
        )
        self._tools["shell_exec"] = ToolDefinition(
            name="shell_exec",
            description=(
                "Run a shell command with cwd=workspace_root, 30s timeout. Destructive / network commands are refused."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command line.",
                    },
                },
                "required": ["command"],
            },
            handler=self._shell_exec,
        )

    def _resolve(self, raw: str) -> Path:
        candidate = (self._root / raw).resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError as exc:
            raise ToolError(f"Path {raw!r} escapes the workspace") from exc
        return candidate

    async def _file_read(self, args: dict[str, Any]) -> str:
        raw_path = str(args.get("path") or "")
        if not raw_path:
            raise ToolError("file_read requires 'path'")
        target = self._resolve(raw_path)
        if not target.exists():
            raise ToolError(f"file_read: not found: {raw_path}")
        if not target.is_file():
            raise ToolError(f"file_read: not a file: {raw_path}")
        return await asyncio.to_thread(_read_text_capped, target, FILE_READ_MAX_BYTES)

    async def _file_list(self, args: dict[str, Any]) -> str:
        raw_path = str(args.get("path") or ".")
        target = self._resolve(raw_path)
        if not target.exists():
            raise ToolError(f"file_list: not found: {raw_path}")
        if not target.is_dir():
            raise ToolError(f"file_list: not a directory: {raw_path}")
        entries = sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir())
        return "\n".join(entries) if entries else "(empty)"

    async def _file_write(self, args: dict[str, Any]) -> str:
        raw_path = str(args.get("path") or "")
        if not raw_path:
            raise ToolError("file_write requires 'path'")
        content = args.get("content")
        if not isinstance(content, str):
            raise ToolError("file_write requires string 'content'")
        if len(content.encode("utf-8")) > FILE_WRITE_MAX_BYTES:
            raise ToolError(f"file_write: content exceeds {FILE_WRITE_MAX_BYTES} bytes")
        target = self._resolve(raw_path)
        await asyncio.to_thread(_write_text, target, content)
        return f"wrote {raw_path} ({len(content)} chars)"

    async def _shell_exec(self, args: dict[str, Any]) -> str:
        command = str(args.get("command") or "")
        if not command.strip():
            raise ToolError("shell_exec requires non-empty 'command'")
        normalized = " " + command.strip() + " "
        for pattern in SHELL_DENYLIST_PATTERNS:
            if pattern in normalized:
                raise ToolError(f"shell_exec: refused by denylist: {pattern.strip()!r}")
        try:
            parts = shlex.split(command)
        except ValueError as exc:
            raise ToolError(f"shell_exec: bad shell syntax: {exc}") from exc
        if not parts:
            raise ToolError("shell_exec: empty command")
        try:
            process = await asyncio.create_subprocess_exec(
                *parts,
                cwd=str(self._root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            return f"command not found: {exc}"
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=SHELL_TIMEOUT_S)
        except TimeoutError:
            process.kill()
            await process.communicate()
            raise ToolError(f"shell_exec: timed out after {SHELL_TIMEOUT_S}s") from None
        output = "\n".join(chunk.decode("utf-8", errors="replace") for chunk in (stdout, stderr) if chunk)
        return f"exit={process.returncode}\n{output[-4000:]}"


def _read_text_capped(path: Path, cap: int) -> str:
    data = path.read_bytes()
    if len(data) > cap:
        return data[:cap].decode("utf-8", errors="replace") + f"\n... (truncated at {cap} bytes)"
    return data.decode("utf-8", errors="replace")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
