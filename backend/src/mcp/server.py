"""FastAPI router that mounts the BSNexus MCP surface.

Two endpoints:

- ``GET /mcp/health?token=...`` — verifies the run-scoped token and
  returns the claim payload. Used by ops to smoke-test that a token
  the dispatcher minted is valid before the BSGateway worker actually
  spins up the CLI. Cheap to call.
- ``ANY /mcp/http?token=...`` — the MCP **streamable-HTTP** endpoint
  the worker's CLI (claude / codex / opencode) connects to. Verifies
  the token, then hands off to FastMCP's streamable-HTTP transport.

The streamable-HTTP transport supersedes the legacy SSE-only transport
(deprecated by the MCP spec, 2024). All three executor CLIs we target
either accept streamable-HTTP natively (claude with ``"type": "http"``,
codex via TOML ``url``) or auto-negotiate streamable-HTTP first then
fall back to SSE (opencode). Hosting one transport keeps the auth gate
simple and avoids two parallel mount paths.

Token verification is shared between both — see :mod:`backend.src.mcp.auth`.

The six tools (``decision.create`` / ``decision.wait`` /
``artifact.list`` / ``artifact.read`` / ``report_deliverable`` /
``knowledge.search``) live in :mod:`backend.src.mcp.tools` as plain
async functions so the unit tests don't have to fight the MCP protocol;
the FastMCP wrapper here is a thin dispatch layer that reads the
per-request auth/DB context from a contextvar.
"""

from __future__ import annotations

import contextvars
from typing import Any
from urllib.parse import parse_qs

import structlog
from bsvibe_authz import User
from fastapi import APIRouter, HTTPException, Query, status

from backend.src.config import settings
from backend.src.mcp.api import ToolContext, ToolRegistry
from backend.src.mcp.auth import (
    MCPAuthError,
    verify_run_scoped_token,
)
from backend.src.mcp.domain_tools import DOMAIN_RUN_SCOPE, register_domain_tools
from backend.src.storage.database import async_session

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/mcp", tags=["mcp"])


# Per-request auth context. Tools read this to know which run / tenant
# / project they're acting on. Set by the SSE handler before
# delegating to the FastMCP server, cleared on disconnect.
_auth_ctx: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar("mcp_auth_ctx")


def _verify(token: str) -> dict[str, Any]:
    try:
        return verify_run_scoped_token(token, signing_key=settings.mcp_signing_key)
    except MCPAuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc


@router.get("/health")
async def mcp_health(token: str = Query(...)) -> dict[str, Any]:
    """Smoke-test that a run-scoped token verifies cleanly. Returns the
    claim payload (run_id / tenant_id / project_id / iat / exp)."""
    claim = _verify(token)
    return {"ok": True, "claim": claim}


_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    """Return the process-wide :class:`ToolRegistry` for MCP dispatch.

    Built lazily on first access so tests that import the module don't
    pay for tool registration up-front. The registry holds every
    domain tool today; admin tools (TASK-004) will register on the
    same instance so HTTP+stdio share one catalog.
    """
    global _registry
    if _registry is None:
        reg = ToolRegistry()
        register_domain_tools(reg)
        _registry = reg
    return _registry


def _build_run_context(claim: dict[str, str], session) -> ToolContext:
    """Build a :class:`ToolContext` for a run-scoped MCP call.

    The synthetic :class:`User` carries ``DOMAIN_RUN_SCOPE`` so the
    registry's scope gate grants every domain tool. Run / project / tenant
    come from the verified token claim — never trusted from input.
    """
    user = User(
        id=f"run:{claim['run_id']}",
        is_service=True,
        scope=[DOMAIN_RUN_SCOPE],
        active_tenant_id=claim["tenant_id"],
    )
    return ToolContext(
        settings=settings,
        user=user,
        db=session,
        audit_session=session,
        logger=logger,
        run_id=claim["run_id"],
        project_id=claim["project_id"],
    )


def _build_fastmcp() -> Any:
    """Construct the FastMCP server with the six BSNexus tools.

    Each ``@app.tool()`` wrapper is a thin transport adapter — it reads
    the verified run-scoped claim from the contextvar (set by
    :func:`attach_to_app`'s ASGI gate), opens a DB session, builds a
    :class:`ToolContext`, and dispatches through the shared
    :class:`ToolRegistry`. The first-class :class:`Tool` definitions
    in :mod:`backend.src.mcp.domain_tools` carry the Pydantic input /
    output schemas, scope requirements, and ``audit_event`` for mutating
    calls — the wrapper here does no business logic.

    ``streamable_http_path="/"`` keeps FastMCP's transport route at the
    mount root. The default ``/mcp`` would push the actual URL to
    ``/mcp/http/mcp`` once mounted under ``/mcp/http``, and
    ``dispatcher.py`` advertises the endpoint as
    ``{base}/mcp/http?token=...`` — that 404s and the LLM tool loop
    silently falls back to no-tools mode (Round 1 finding 2026-05-07).
    """
    from mcp.server.fastmcp import FastMCP  # noqa: PLC0415

    app = FastMCP("bsnexus", streamable_http_path="/")
    registry = get_registry()

    @app.tool()
    async def decision_create(
        question: str, options: list[str] | None = None, context: str | None = None
    ) -> dict[str, str]:
        """Open a Decision row for the founder to resolve. Returns ``{decision_id}``."""
        claim = _auth_ctx.get()
        async with async_session() as session:
            ctx = _build_run_context(claim, session)
            result = await registry.call_tool(
                "decision_create",
                {"question": question, "options": options or [], "context": context},
                ctx,
            )
            await session.commit()
        return result.model_dump()

    @app.tool()
    async def decision_wait(decision_id: str, timeout_seconds: float = 3000.0) -> dict[str, Any]:
        """Block until the founder resolves the decision. Returns ``{choice, notes}`` or ``{error}``."""
        claim = _auth_ctx.get()
        async with async_session() as session:
            ctx = _build_run_context(claim, session)
            result = await registry.call_tool(
                "decision_wait",
                {"decision_id": decision_id, "timeout_seconds": timeout_seconds},
                ctx,
            )
        # ``exclude_none`` keeps the wire shape compatible with the
        # previous wrapper which returned ``{choice, notes}`` on success
        # and ``{error}`` on failure — never the union with all fields.
        return result.model_dump(exclude_none=True)

    @app.tool()
    async def artifact_list(request_id: str) -> list[dict[str, Any]]:
        """List deliverables for a request. Returns ``[{id, title, type, status}]``."""
        claim = _auth_ctx.get()
        async with async_session() as session:
            ctx = _build_run_context(claim, session)
            result = await registry.call_tool("artifact_list", {"request_id": request_id}, ctx)
        # FastMCP wire shape pinned by callers is a bare list; unwrap the
        # ``items`` envelope from the typed output.
        return [item.model_dump() for item in result.items]

    @app.tool()
    async def deliverable_report(title: str, body: str, links: list[str] | None = None) -> dict[str, str]:
        """Persist a Deliverable + DeliverableVersion. Returns ``{deliverable_id}``."""
        claim = _auth_ctx.get()
        async with async_session() as session:
            ctx = _build_run_context(claim, session)
            result = await registry.call_tool(
                "deliverable_report",
                {"title": title, "body": body, "links": links},
                ctx,
            )
            await session.commit()
        return result.model_dump()

    @app.tool()
    async def artifact_read(deliverable_id: str) -> dict[str, str]:
        """Return the inline body of a Deliverable's current version.
        Returns ``{body}`` (may include ``error`` for non-inline / missing)."""
        claim = _auth_ctx.get()
        async with async_session() as session:
            ctx = _build_run_context(claim, session)
            result = await registry.call_tool("artifact_read", {"deliverable_id": deliverable_id}, ctx)
        return result.model_dump(exclude_none=True)

    @app.tool()
    async def knowledge_search(query: str, top_k: int = 10) -> list[dict[str, str]]:
        """Search BSage. Returns ``[{title, excerpt}]`` (empty if BSage disabled)."""
        claim = _auth_ctx.get()
        async with async_session() as session:
            ctx = _build_run_context(claim, session)
            result = await registry.call_tool("knowledge_search", {"query": query, "top_k": top_k}, ctx)
        return [hit.model_dump() for hit in result.hits]

    return app


_fastmcp_app: Any | None = None


def _get_fastmcp() -> Any:
    global _fastmcp_app
    if _fastmcp_app is None:
        _fastmcp_app = _build_fastmcp()
    return _fastmcp_app


def fastmcp_session_manager_run():
    """Return ``session_manager.run()`` async context for the singleton
    FastMCP app.

    FastMCP's streamable-HTTP transport spins up an internal
    ``StreamableHTTPSessionManager`` that owns its task group; without
    entering ``session_manager.run()`` once at process startup, every
    request handler raises ``RuntimeError: Task group is not initialized.
    Make sure to use run().``. When mounting the transport sub-app under
    a parent FastAPI we must chain this context into the parent's
    lifespan ourselves — Starlette mounts don't propagate sub-app
    lifespans automatically. ``attach_to_app`` already builds the
    transport app (which lazily creates the session_manager), so by the
    time the parent lifespan calls this, the manager exists.
    """
    fastmcp = _get_fastmcp()
    # ``streamable_http_app()`` is what creates ``session_manager``
    # lazily. ``attach_to_app`` already invoked it; calling again is
    # idempotent (returns the cached app) but safe regardless.
    fastmcp.streamable_http_app()
    return fastmcp.session_manager.run()


def attach_to_app(app) -> None:  # type: ignore[no-untyped-def]
    """Mount the MCP streamable-HTTP app on the main FastAPI app under
    ``/mcp/http``.

    Called from ``main.create_app`` after the routers are added. The
    transport app needs token verification per-connect — implemented
    as a Starlette middleware that reads the ``token`` query param,
    verifies it, and stuffs the claim into ``_auth_ctx`` before yielding
    to FastMCP's transport.

    NOTE: also wire ``fastmcp_session_manager_run()`` into the parent
    app's lifespan (see ``main.lifespan``). The mount alone routes
    requests but the session manager's task group still has to be
    started, or every request 500s with "Task group is not initialized".
    """
    from starlette.types import Receive, Scope, Send  # noqa: PLC0415

    fastmcp = _get_fastmcp()
    transport_app = fastmcp.streamable_http_app()

    async def _gated(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await transport_app(scope, receive, send)
            return
        # Parse ``token=...`` from the query string. ``parse_qs`` handles
        # percent-decoding, repeated keys, and value-less keys correctly —
        # the ad-hoc ``split("&") / split("=", 1)`` parser this replaces
        # would mangle a ``%XX``-encoded base64url char (the JWT-style
        # token uses ``.``/``-``/``_`` only, but the signing key choice
        # could change) and silently keep the *first* value of a
        # ``token=a&token=b`` smuggling attempt instead of rejecting.
        qs = scope.get("query_string", b"").decode("ascii", errors="replace")
        params = parse_qs(qs, keep_blank_values=True, strict_parsing=False)
        token_values = params.get("token", [])
        # Reject if a caller supplies two ``token=`` keys — there's no
        # legitimate use for that and treating it as authoritative-first
        # is a smuggling foothold.
        if len(token_values) != 1:
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [(b"content-type", b"text/plain")],
                }
            )
            await send({"type": "http.response.body", "body": b"missing or duplicate token"})
            return
        token = token_values[0]
        try:
            claim = verify_run_scoped_token(token, signing_key=settings.mcp_signing_key)
        except MCPAuthError as exc:
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [(b"content-type", b"text/plain")],
                }
            )
            await send({"type": "http.response.body", "body": str(exc).encode()})
            return
        token_value = _auth_ctx.set(
            {
                "run_id": claim["run_id"],
                "tenant_id": claim["tenant_id"],
                "project_id": claim["project_id"],
            }
        )
        try:
            await transport_app(scope, receive, send)
        finally:
            _auth_ctx.reset(token_value)

    app.mount("/mcp/http", _gated)
