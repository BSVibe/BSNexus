"""``bsnexus mcp`` sub-app — local MCP catalog + stdio launcher.

Two commands:

* ``bsnexus mcp list-tools`` — enumerate the shared
  :class:`~backend.src.mcp.api.ToolRegistry` (domain + admin) without
  any HTTP round-trip. Useful for confirming the catalog after a deploy.
* ``bsnexus mcp serve --transport stdio|http`` — boot the same
  registry. ``stdio`` runs FastMCP's stdio server for direct integration
  with claude / codex / opencode CLIs; ``http`` is documented as a
  pointer at the embedded ``/mcp/admin`` endpoints since spinning up a
  second uvicorn would duplicate the lifespan-mounted server. The
  bootstrap admin token comes from ``BSV_BOOTSTRAP_TOKEN`` and is never
  echoed.
"""

from __future__ import annotations

import os
from typing import Any

import structlog
import typer

from backend.src.cli.commands._common import emit_dry_run, run_async

app = typer.Typer(
    name="mcp",
    help="MCP server catalog + stdio launcher.",
    no_args_is_help=True,
    add_completion=False,
)

logger = structlog.get_logger(__name__)


_VALID_TRANSPORTS = ("stdio", "http")


def _registry() -> Any:
    """Lazy import so unrelated CLI commands don't pay the registry
    import cost at startup."""
    from backend.src.mcp.server import get_registry  # noqa: PLC0415

    return get_registry()


@app.command("list-tools", help="List every registered MCP tool (domain + admin).")
def list_tools(ctx: typer.Context) -> None:
    """Render the catalog locally — no HTTP. ``--output json`` returns a
    structured list including ``required_scopes`` and ``audit_event``;
    table mode shows just name + description."""
    reg = _registry()
    tools = []
    for name in reg.names():
        tool = reg.get(name)
        tools.append(
            {
                "name": tool.name,
                "description": tool.description,
                "required_scopes": list(tool.required_scopes),
                "audit_event": tool.audit_event,
            }
        )
    ctx.obj.formatter.emit({"tools": tools})


@app.command("serve", help="Run the MCP server (stdio or http transport).")
def serve(
    ctx: typer.Context,
    transport: str = typer.Option(
        "stdio",
        "--transport",
        help="Transport to run. Allowed: stdio, http.",
    ),
) -> None:
    """Boot the registry and run the requested transport.

    ``--dry-run`` (global flag) skips the actual run loop and prints
    the planned launch shape — used by tests and by operators sanity-
    checking auth wiring. ``BSV_BOOTSTRAP_TOKEN`` is never echoed —
    only its presence is reported.
    """
    if transport not in _VALID_TRANSPORTS:
        typer.echo(
            f"Error: invalid transport {transport!r}. Allowed: {', '.join(_VALID_TRANSPORTS)}.",
            err=True,
        )
        raise typer.Exit(code=2)

    bootstrap_present = bool(os.environ.get("BSV_BOOTSTRAP_TOKEN"))
    plan = {
        "transport": transport,
        "bootstrap_token_present": bootstrap_present,
    }

    if ctx.obj.dry_run:
        emit_dry_run(ctx.obj, plan)
        return

    if transport == "http":
        # The HTTP transport is owned by the lifespan-mounted FastAPI
        # app. Telling the operator where it lives is more useful than
        # spinning up a second uvicorn that duplicates the same
        # registry.
        ctx.obj.formatter.emit(
            {
                "transport": "http",
                "endpoint": "/mcp/admin/tools",
                "note": "HTTP transport runs in the BSNexus FastAPI process; this command is informational only.",
            }
        )
        return

    # stdio path — block on FastMCP's stdio loop. We log "starting"
    # without echoing the token; bootstrap auth flows through every
    # subsequent dispatch via the resolve_tool_context env path.
    if not bootstrap_present:
        logger.warning("mcp_serve_stdio_no_bootstrap_token")

    from backend.src.mcp.server import _get_fastmcp  # noqa: PLC0415

    fastmcp = _get_fastmcp()
    run_async(fastmcp.run_stdio_async)
