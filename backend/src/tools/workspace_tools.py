"""Workspace tools — file read, write, list for project workspaces.

All paths are relative to ``ctx.workspace_path``. Path traversal is
blocked by resolving the full path and checking it stays under the root.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from backend.src.tools.base import Tool, ToolContext, ToolExecutionError

# Hard limit to avoid dumping huge files into LLM context.
MAX_READ_BYTES = 128 * 1024  # 128 KB
MAX_WRITE_BYTES = 512 * 1024  # 512 KB


def _safe_resolve(relative: str, workspace: Path) -> Path:
    """Resolve a relative path under workspace, blocking traversal."""
    resolved = (workspace / relative).resolve()
    workspace_resolved = workspace.resolve()
    if not str(resolved).startswith(str(workspace_resolved)):
        raise ToolExecutionError(f"Path traversal not allowed: {relative}")
    return resolved


class FileReadTool(Tool):
    @property
    def name(self) -> str:
        return "file_read"

    @property
    def description(self) -> str:
        return (
            "Read the contents of a file in the project workspace. "
            "Returns the text content. Use offset/limit for large files."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to workspace root",
                },
                "offset": {
                    "type": "integer",
                    "description": "Start reading from this line number (0-indexed). Default 0.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to return. Default: all.",
                },
            },
            "required": ["path"],
        }

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str:
        path = _safe_resolve(input["path"], ctx.workspace_path)
        if not path.is_file():
            raise ToolExecutionError(f"File not found: {input['path']}")

        stat = path.stat()
        if stat.st_size > MAX_READ_BYTES:
            raise ToolExecutionError(
                f"File too large ({stat.st_size:,} bytes). Use offset/limit or read a smaller file."
            )

        raw = await asyncio.to_thread(path.read_text, "utf-8")
        lines = raw.splitlines(keepends=True)

        offset = input.get("offset", 0)
        limit = input.get("limit")
        if limit is not None:
            lines = lines[offset : offset + limit]
        elif offset > 0:
            lines = lines[offset:]

        return "".join(lines)


class FileWriteTool(Tool):
    @property
    def name(self) -> str:
        return "file_write"

    @property
    def description(self) -> str:
        return (
            "Write content to a file in the project workspace. "
            "Creates parent directories if needed. Overwrites if file exists."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to workspace root",
                },
                "content": {
                    "type": "string",
                    "description": "Full file content to write",
                },
            },
            "required": ["path", "content"],
        }

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str:
        content = input.get("content") or input.get("text") or ""
        if not content:
            raise ToolExecutionError("Missing required 'content' field. Provide the file content to write.")
        if len(content.encode("utf-8")) > MAX_WRITE_BYTES:
            raise ToolExecutionError(
                f"Content too large ({len(content.encode('utf-8')):,} bytes, max {MAX_WRITE_BYTES:,})."
            )

        path = _safe_resolve(input["path"], ctx.workspace_path)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_text, content, "utf-8")
        return f"Written {len(content)} chars to {input['path']}"


class ListFilesTool(Tool):
    @property
    def name(self) -> str:
        return "list_files"

    @property
    def description(self) -> str:
        return (
            "List files and directories in the project workspace. "
            "Returns one entry per line with [DIR] or [FILE] prefix and size."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path relative to workspace root. Default: root.",
                    "default": "",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "List recursively. Default: false.",
                    "default": False,
                },
            },
        }

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str:
        rel = input.get("path", "")
        target = _safe_resolve(rel, ctx.workspace_path) if rel else ctx.workspace_path.resolve()
        if not target.is_dir():
            raise ToolExecutionError(f"Not a directory: {rel or '/'}")

        recursive = input.get("recursive", False)

        def _scan() -> list[str]:
            entries: list[str] = []
            if recursive:
                for dirpath, dirnames, filenames in os.walk(target):
                    # Skip hidden directories
                    dirnames[:] = [d for d in sorted(dirnames) if not d.startswith(".")]
                    rel_dir = os.path.relpath(dirpath, ctx.workspace_path.resolve())
                    for d in sorted(dirnames):
                        p = os.path.join(rel_dir, d) if rel_dir != "." else d
                        entries.append(f"[DIR]  {p}/")
                    for f in sorted(filenames):
                        if f.startswith("."):
                            continue
                        full = os.path.join(dirpath, f)
                        size = os.path.getsize(full)
                        p = os.path.join(rel_dir, f) if rel_dir != "." else f
                        entries.append(f"[FILE] {p} ({size:,} bytes)")
                    if len(entries) > 500:
                        entries.append("... (truncated at 500 entries)")
                        break
            else:
                with os.scandir(target) as it:
                    for entry in sorted(it, key=lambda e: (not e.is_dir(), e.name)):
                        if entry.name.startswith("."):
                            continue
                        rp = os.path.relpath(entry.path, ctx.workspace_path.resolve())
                        if entry.is_dir():
                            entries.append(f"[DIR]  {rp}/")
                        else:
                            entries.append(f"[FILE] {rp} ({entry.stat().st_size:,} bytes)")
            return entries

        entries = await asyncio.to_thread(_scan)
        if not entries:
            return f"Directory is empty: {rel or '/'}"
        return "\n".join(entries)
