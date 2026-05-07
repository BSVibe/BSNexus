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
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Query, status

from backend.src.config import settings
from backend.src.core.composer.knowledge_client import (
    KnowledgeClient,
    NoopKnowledgeClient,
)
from backend.src.mcp.auth import (
    MCPAuthError,
    verify_run_scoped_token,
)
from backend.src.mcp.decision_queue import get_decision_queue
from backend.src.mcp.tools import (
    MCPToolError,
    create_decision,
    list_run_artifacts,
    read_artifact,
    report_deliverable,
    search_knowledge,
    wait_for_decision,
)
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


def _build_fastmcp() -> Any:
    """Construct the FastMCP server with the six BSNexus tools.

    Tools read the per-request auth context from ``_auth_ctx``; the SSE
    handler sets it before yielding to FastMCP's transport.
    """
    from mcp.server.fastmcp import FastMCP  # noqa: PLC0415

    # ``streamable_http_path="/"`` keeps FastMCP's transport route at
    # the mount root. The default ``/mcp`` would push the actual URL to
    # ``/mcp/http/mcp`` once mounted under ``/mcp/http`` (see
    # ``attach_to_app``), and dispatcher.py advertises the endpoint as
    # ``{base}/mcp/http?token=...`` — that 404s and the LLM tool loop
    # silently falls back to no-tools mode (Round 1 finding 2026-05-07,
    # ``tools_in_kwargs: false`` for every run).
    app = FastMCP("bsnexus", streamable_http_path="/")

    @app.tool()
    async def decision_create(
        question: str, options: list[str] | None = None, context: str | None = None
    ) -> dict[str, str]:
        """Open a Decision row for the founder to resolve. Returns ``{decision_id}``."""
        ctx = _auth_ctx.get()
        async with async_session() as session:
            decision_id = await create_decision(
                question=question,
                options=options or [],
                context=context,
                run_id=UUID(ctx["run_id"]),
                tenant_id=UUID(ctx["tenant_id"]),
                project_id=UUID(ctx["project_id"]),
                db=session,
            )
            await session.commit()
        return {"decision_id": str(decision_id)}

    @app.tool()
    async def decision_wait(decision_id: str, timeout_seconds: float = 3000.0) -> dict[str, Any]:
        """Block until the founder resolves the decision. Returns ``{choice, notes}``."""
        ctx = _auth_ctx.get()
        async with async_session() as session:
            try:
                return await wait_for_decision(
                    decision_id=UUID(decision_id),
                    tenant_id=UUID(ctx["tenant_id"]),
                    queue=get_decision_queue(),
                    timeout_seconds=timeout_seconds,
                    db=session,
                )
            except MCPToolError as exc:
                # MCP convention: tool errors are surfaced as the result
                # rather than the transport blowing up — claude can
                # decide how to handle.
                return {"error": str(exc)}

    @app.tool()
    async def artifact_list(request_id: str) -> list[dict[str, Any]]:
        """List deliverables for a request. Returns ``[{id, title, type, status}]``."""
        ctx = _auth_ctx.get()
        async with async_session() as session:
            return await list_run_artifacts(
                request_id=UUID(request_id),
                tenant_id=UUID(ctx["tenant_id"]),
                db=session,
            )

    @app.tool()
    async def deliverable_report(title: str, body: str, links: list[str] | None = None) -> dict[str, str]:
        """Persist a Deliverable + DeliverableVersion. Returns ``{deliverable_id}``."""
        ctx = _auth_ctx.get()
        async with async_session() as session:
            deliv_id = await report_deliverable(
                title=title,
                body=body,
                links=links,
                run_id=UUID(ctx["run_id"]),
                tenant_id=UUID(ctx["tenant_id"]),
                project_id=UUID(ctx["project_id"]),
                db=session,
            )
            await session.commit()
        return {"deliverable_id": str(deliv_id)}

    @app.tool()
    async def artifact_read(deliverable_id: str) -> dict[str, str]:
        """Return the inline body of a Deliverable's current version.
        Returns ``{body}`` (may be empty for non-inline / unversioned)."""
        ctx = _auth_ctx.get()
        async with async_session() as session:
            try:
                body = await read_artifact(
                    deliverable_id=UUID(deliverable_id),
                    tenant_id=UUID(ctx["tenant_id"]),
                    db=session,
                )
            except MCPToolError as exc:
                return {"error": str(exc), "body": ""}
        return {"body": body}

    @app.tool()
    async def knowledge_search(query: str, top_k: int = 10) -> list[dict[str, str]]:
        """Search BSage. Returns ``[{title, excerpt}]`` (empty if BSage disabled)."""
        # BSage routing requires the tenant integration snapshot, which
        # we resolve fresh so the tool stays correct across config edits.
        from backend.src.core.composer import resolve_knowledge_client  # noqa: PLC0415
        from backend.src.core.integrations import get_tenant_integration_snapshot  # noqa: PLC0415

        ctx = _auth_ctx.get()
        async with async_session() as session:
            integrations = await get_tenant_integration_snapshot(session, UUID(ctx["tenant_id"]))
            knowledge: KnowledgeClient = resolve_knowledge_client(integrations.bsage)

        if isinstance(knowledge, NoopKnowledgeClient):
            return []
        return await search_knowledge(query=query, knowledge_client=knowledge, top_k=top_k)

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
