"""``build_server`` — wrap a :class:`ToolRegistry` behind ``mcp.server.Server``.

Round 4 lessons folded in:

* F22 — ``_call_tool`` wraps the registry's dict result in a list of
  ``TextContent`` blocks. The MCP SDK strictly validates
  ``CallToolResult.content`` against the ContentBlock union; plain dicts
  fail with the "72 validation errors" envelope on the wire.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from mcp.server import Server
from mcp.types import TextContent
from mcp.types import Tool as McpTool

from backend.src.admin_mcp.api import ToolContext, ToolError, ToolRegistry

logger = structlog.get_logger(__name__)


ContextProvider = Callable[[], Awaitable[ToolContext]]
DEFAULT_SERVER_NAME = "bsnexus"


def build_server(
    registry: ToolRegistry,
    *,
    context_provider: ContextProvider,
    server_name: str = DEFAULT_SERVER_NAME,
) -> Server:
    """Construct an MCP ``Server`` that delegates to ``registry``.

    The SDK auto-translates exceptions raised inside ``call_tool`` into
    ``CallToolResult(isError=True, ...)``.
    """
    server: Server = Server(server_name)

    @server.list_tools()
    async def _list_tools() -> list[McpTool]:
        return registry.list_tools()

    @server.call_tool(validate_input=False)
    async def _call_tool(tool_name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
        ctx = await context_provider()
        try:
            result = await registry.call_tool(tool_name, arguments or {}, ctx)
        except ToolError as exc:
            # Surface the typed code on the wire — let the SDK wrap as isError=True
            raise RuntimeError(f"{exc.code}: {exc.message}") from exc
        return [TextContent(type="text", text=json.dumps(result, default=str))]

    return server


__all__ = ["ContextProvider", "DEFAULT_SERVER_NAME", "build_server"]
