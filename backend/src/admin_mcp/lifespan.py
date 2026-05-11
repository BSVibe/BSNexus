"""BSNexus admin MCP lifespan integration.

Mounts a streamable-HTTP MCP server at ``/mcp/`` alongside the existing
FastAPI app. Admin tools share the SAME REST request handlers the CLI
hits — the loopback caller drives the FastAPI app in-process via
``httpx.ASGITransport`` so there is zero router-logic duplication.

Round 4 lessons folded in:

* F15 — loopback forwards the user's ``Authorization`` header from the
  MCP request context-var so admin REST routes that ``Depends`` on the
  auth dispatcher authenticate as the same principal the MCP transport
  already verified.
* F20 — non-2xx responses translate to typed :class:`ToolError` with
  redacted messages; the internal ``http://mcp-loopback`` URL never
  reaches the wire.
* F23 — DB session is threaded into ``ctx.app_state`` via the FastAPI
  lifespan so handlers can read ``app_state.engine`` / session factory.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any, AsyncIterator

import httpx
import structlog
from bsvibe_authz import IntrospectionClient
from bsvibe_authz import Settings as AuthzSettings
from bsvibe_authz.cache import IntrospectionCache
from bsvibe_authz.deps import get_settings as get_authz_settings
from fastapi import FastAPI
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from backend.src.admin_mcp.api import (
    ToolContext,
    ToolError,
    ToolRegistry,
    resolve_tool_context,
)
from backend.src.admin_mcp.server import build_server

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Loopback caller — ASGI transport against the running FastAPI app
# ---------------------------------------------------------------------------


LoopbackCaller = Callable[..., Awaitable[Any]]


_LOOPBACK_STATUS_TO_TOOL_CODE: dict[int, str] = {
    400: "invalid_input",
    401: "unauthenticated",
    403: "permission_denied",
    404: "not_found",
    409: "conflict",
    422: "invalid_input",
    429: "rate_limited",
}


def _translate_loopback_error(resp: httpx.Response, method: str, path: str) -> ToolError:
    """Build a redacted ToolError for a non-2xx loopback response.

    Mirrors the F20 fix in BSGateway: the wire message MUST NOT carry the
    internal ``http://mcp-loopback`` URL or the MDN docs hint.
    """
    code = _LOOPBACK_STATUS_TO_TOOL_CODE.get(resp.status_code, "internal_error")
    detail: str | None = None
    try:
        body = resp.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        raw_detail = body.get("detail")
        if isinstance(raw_detail, str) and raw_detail:
            detail = raw_detail
    message = detail or f"upstream returned HTTP {resp.status_code}"
    logger.info(
        "mcp_loopback_error",
        status=resp.status_code,
        method=method,
        path=path,
        code=code,
    )
    return ToolError(code=code, message=message)


def make_loopback_caller(
    app: FastAPI,
    *,
    base_url: str = "http://mcp-loopback",
) -> LoopbackCaller:
    """Return a :data:`LoopbackCaller` driven by ``httpx.ASGITransport``.

    Forwards the resolved active tenant (``X-Tenant-ID``) and the user's
    ``Authorization`` header from ``_request_headers_var`` so admin REST
    routes authenticate as the same principal the MCP transport verified
    (F15 mirror).
    """

    async def caller(
        ctx: Any,
        method: str,
        path: str,
        *,
        body: Any | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        merged_headers: dict[str, str] = {}
        active_tenant = getattr(getattr(ctx, "user", None), "active_tenant_id", None)
        if active_tenant is not None:
            merged_headers["X-Tenant-ID"] = str(active_tenant)
        incoming = _request_headers_var.get() or {}
        incoming_auth = incoming.get("authorization") or incoming.get("Authorization")
        if incoming_auth:
            merged_headers["Authorization"] = incoming_auth
        if headers:
            merged_headers.update(headers)

        full_path = f"/api/v1{path}" if path.startswith("/") else f"/api/v1/{path}"

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=base_url) as client:
            resp = await client.request(
                method,
                full_path,
                json=body,
                params=params,
                headers=merged_headers or None,
            )
        if resp.status_code >= 400:
            raise _translate_loopback_error(resp, method, path)
        if resp.status_code == 204 or not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    return caller


# ---------------------------------------------------------------------------
# Per-request header context-var (captured by the ASGI shim)
# ---------------------------------------------------------------------------


_request_headers_var: ContextVar[Mapping[str, str] | None] = ContextVar(
    "_bsnexus_mcp_request_headers",
    default=None,
)


# ---------------------------------------------------------------------------
# Lifespan integration
# ---------------------------------------------------------------------------


def _build_introspection_inputs() -> tuple[AuthzSettings | None, IntrospectionClient | None, IntrospectionCache]:
    """Resolve bsvibe-authz Settings + introspection helpers.

    Returns ``(None, None, fresh-cache)`` when the env vars for
    introspection are missing — bootstrap-only deployments stay bootable.
    """
    from pydantic import ValidationError

    try:
        authz_settings: AuthzSettings | None = get_authz_settings()
    except ValidationError as exc:
        logger.info("mcp_authz_settings_unavailable", reason="missing_env", missing=str(exc))
        return None, None, IntrospectionCache(ttl_s=60)

    introspection_client: IntrospectionClient | None = None
    if authz_settings.introspection_url:
        introspection_client = IntrospectionClient(
            introspection_url=authz_settings.introspection_url,
            client_id=authz_settings.introspection_client_id or "",
            client_secret=authz_settings.introspection_client_secret or "",
        )
    introspection_cache = IntrospectionCache(
        ttl_s=authz_settings.permission_cache_ttl_s,
    )
    return authz_settings, introspection_client, introspection_cache


@asynccontextmanager
async def admin_mcp_lifespan(
    app: FastAPI,
    *,
    registry: ToolRegistry,
) -> AsyncIterator[ToolRegistry]:
    """Wire the streamable-HTTP MCP server into the FastAPI lifespan."""

    async def _context_provider() -> ToolContext:
        authz_settings, introspection_client, introspection_cache = _build_introspection_inputs()
        if authz_settings is None:
            raise ToolError(code="unauthenticated", message="authz settings unavailable")
        headers = _request_headers_var.get() or {}
        return await resolve_tool_context(
            headers,
            app_state=app.state,
            settings=authz_settings,
            introspection_client=introspection_client,
            introspection_cache=introspection_cache,
        )

    server = build_server(registry, context_provider=_context_provider)
    manager = StreamableHTTPSessionManager(app=server, stateless=True, json_response=True)

    app.state.admin_mcp_registry = registry
    app.state.admin_mcp_session_manager = manager

    logger.info("admin_mcp_lifespan_starting", tool_count=len(registry.names()))

    async with manager.run():
        try:
            yield registry
        finally:
            logger.info("admin_mcp_lifespan_stopping")


def build_streamable_http_asgi_app(parent_app: FastAPI) -> Callable[..., Awaitable[None]]:
    """Return the ASGI handler that captures incoming HTTP headers into
    ``_request_headers_var`` before delegating to the SDK's manager."""

    async def asgi_app(scope: Any, receive: Any, send: Any) -> None:
        manager: StreamableHTTPSessionManager | None = getattr(parent_app.state, "admin_mcp_session_manager", None)
        if manager is None:
            raise RuntimeError("admin_mcp_session_manager not set on app.state")
        if scope.get("type") != "http":
            await manager.handle_request(scope, receive, send)
            return
        raw_headers = scope.get("headers", []) or []
        decoded: dict[str, str] = {}
        for k, v in raw_headers:
            try:
                decoded[k.decode("latin-1").lower()] = v.decode("latin-1")
            except (AttributeError, UnicodeDecodeError):  # pragma: no cover
                continue
        token = _request_headers_var.set(decoded)
        try:
            await manager.handle_request(scope, receive, send)
        finally:
            _request_headers_var.reset(token)

    return asgi_app


__all__ = [
    "LoopbackCaller",
    "ToolContext",
    "ToolRegistry",
    "admin_mcp_lifespan",
    "build_streamable_http_asgi_app",
    "make_loopback_caller",
]
