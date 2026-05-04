"""FastAPI router that mounts the BSNexus MCP surface.

Two endpoints:

- ``GET /mcp/health?token=...`` — verifies the run-scoped token and
  returns the claim payload. Used by ops to smoke-test that a token
  the dispatcher minted is valid before the BSGateway worker actually
  spins up claude. Cheap to call.
- ``GET /mcp/sse?token=...`` — the MCP Server-Sent-Events endpoint
  the worker's claude CLI connects to via ``--mcp-config``. Verifies
  the token, then hands off to the FastMCP SSE transport.

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

    app = FastMCP("bsnexus")

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
    async def deliverable_report(
        title: str, body: str, links: list[str] | None = None
    ) -> dict[str, str]:
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
            integrations = await get_tenant_integration_snapshot(
                session, UUID(ctx["tenant_id"])
            )
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


def attach_to_app(app) -> None:  # type: ignore[no-untyped-def]
    """Mount the MCP SSE app on the main FastAPI app under ``/mcp/sse``.

    Called from ``main.create_app`` after the routers are added. The
    SSE app needs token verification per-connect — implemented as a
    Starlette middleware that reads the ``token`` query param, verifies
    it, and stuffs the claim into ``_auth_ctx`` before yielding to
    FastMCP's transport.
    """
    from starlette.types import Receive, Scope, Send  # noqa: PLC0415

    fastmcp = _get_fastmcp()
    sse_app = fastmcp.sse_app()

    async def _gated(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await sse_app(scope, receive, send)
            return
        # Parse ``token=...`` from the query string.
        qs = scope.get("query_string", b"").decode()
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        token = params.get("token", "")
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
            await sse_app(scope, receive, send)
        finally:
            _auth_ctx.reset(token_value)

    app.mount("/mcp/sse", _gated)
