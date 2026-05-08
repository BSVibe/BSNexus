"""First-class MCP API primitives — :class:`Tool`, :class:`ToolContext`,
and :class:`ToolRegistry`.

Phase 7 architectural decision (2026-05-08): MCP is a first-class API
surface alongside REST, not a wrapper around the Typer CLI. Every tool
declares Pydantic input/output schemas, an async handler, the scopes it
requires, and (for mutations) the audit event type to emit on success.
The registry validates input → enforces scopes → runs the handler →
validates output → emits the audit event. All errors map to typed
:class:`ToolError` subclasses so transports can surface them as MCP
error responses without leaking internals.

Two transports both consume the same registry:

  * HTTP — mounted under ``/mcp`` in the FastAPI lifespan (TASK-005).
    Auth context comes from :func:`resolve_tool_context` which mirrors
    the REST 3-way bootstrap-token / opaque-introspection / user-JWT
    dispatch from :mod:`backend.src.core.auth`.
  * stdio — ``bsnexus mcp serve --transport stdio`` (TASK-005). Auth
    context comes from ``BSV_BOOTSTRAP_TOKEN`` env, also fed through
    :func:`resolve_tool_context`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import structlog
from bsvibe_authz import (
    AuthError,
    User,
    verify_bootstrap_token,
    verify_opaque_token,
    verify_user_jwt,
)
from mcp import types as mcp_types
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

# Re-imported via module-level binding so tests can monkeypatch
# ``backend.src.mcp.api.safe_emit`` without reaching into the audit
# package internals.
from backend.src.core.audit.emitter import actor_from_user, safe_emit
from backend.src.core.auth import (
    BOOTSTRAP_TOKEN_PREFIX,
    OPAQUE_TOKEN_PREFIX,
    _authz_settings,
    _get_introspection_cache,
    _get_introspection_client,
)

# Lazily resolved at import time so the ``bsvibe_audit`` event class
# doesn't have to be threaded through every call site.
from bsvibe_audit import AuditEventBase

logger = structlog.get_logger(__name__)


# ── error hierarchy ────────────────────────────────────────────────


class ToolError(Exception):
    """Base for every dispatcher-side failure.

    Transports translate these to MCP error responses. The error message
    is what the MCP caller sees — never include raw token material or
    internal exception detail.
    """


class ToolNotFoundError(ToolError):
    """Caller asked for a tool that isn't registered."""


class ToolValidationError(ToolError):
    """Input or output failed Pydantic validation."""


class ToolPermissionError(ToolError):
    """Auth resolved but the principal is missing a required scope."""


class ToolHandlerError(ToolError):
    """Handler raised an unexpected exception. Class name only —
    the original message is *not* echoed (avoids token / PII leaks).
    """


# ── core dataclasses ───────────────────────────────────────────────


@dataclass
class ToolContext:
    """Per-call context passed to every handler.

    ``settings`` is the BSNexus :class:`Settings` instance (or a test
    double). ``user`` is the resolved :class:`bsvibe_authz.User` —
    bootstrap/opaque/JWT all converge on this shape. ``db`` is for tools
    that read/write domain rows; ``audit_session`` is for the audit
    outbox INSERT (often the same session, but kept distinct so a
    handler that opens its own scoped session can still hand the
    dispatcher a session for the audit row).

    ``run_id`` / ``project_id`` are populated for run-scoped
    (domain-tool) callers; they come from the run-scoped HMAC token
    claim that the FastMCP transport gate verifies before dispatch.
    Admin-tool callers leave them ``None``.
    """

    settings: Any
    user: User
    db: AsyncSession | None = None
    audit_session: AsyncSession | None = None
    logger: structlog.stdlib.BoundLogger | None = None
    run_id: str | None = None
    project_id: str | None = None


ToolHandler = Callable[[BaseModel, ToolContext], Awaitable[BaseModel]]


@dataclass
class Tool:
    """A first-class MCP tool.

    ``input_schema`` / ``output_schema`` are Pydantic model classes — the
    JSON schema returned by ``ListTools`` is derived directly from them
    via ``model_json_schema()``. No auto-derivation from Typer commands;
    every tool is declared explicitly.
    """

    name: str
    description: str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    handler: ToolHandler
    required_scopes: list[str] = field(default_factory=list)
    audit_event: str | None = None


# ── scope helper ───────────────────────────────────────────────────


def _scope_grants(user_scopes: list[str], required: str) -> bool:
    """Mirror of :func:`bsvibe_authz.deps._scope_grants` — see its
    docstring for the rules. Reimplemented locally so MCP doesn't reach
    into a private symbol of the authz package.
    """
    for granted in user_scopes:
        if granted == "*" or granted == required:
            return True
        if granted.endswith(":*") and required.startswith(granted[:-1]):
            return True
    return False


# ── registry ───────────────────────────────────────────────────────


class ToolRegistry:
    """Holds every domain + admin :class:`Tool` and dispatches calls."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    # -- registration --

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ToolError(f"tool {tool.name!r} already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotFoundError(f"unknown tool: {name}") from exc

    def names(self) -> list[str]:
        return list(self._tools.keys())

    # -- ListTools / wire shape --

    def list_tools(self) -> list[mcp_types.Tool]:
        """Return the wire-shape ``mcp.types.Tool`` list ``ListTools``
        responds with — JSON Schema comes straight from each tool's
        Pydantic models.
        """
        return [
            mcp_types.Tool(
                name=t.name,
                description=t.description,
                inputSchema=t.input_schema.model_json_schema(),
                outputSchema=t.output_schema.model_json_schema(),
            )
            for t in self._tools.values()
        ]

    # -- CallTool --

    async def call_tool(
        self,
        name: str,
        args: Mapping[str, Any],
        ctx: ToolContext,
    ) -> BaseModel:
        """Validate input → enforce scopes → run handler → validate
        output → emit audit (on success only). Raises a typed
        :class:`ToolError` on any failure.
        """
        tool = self.get(name)
        log = ctx.logger or logger

        # Scope gate runs first. We don't validate input before scope —
        # an unauthorized caller shouldn't learn about the schema by
        # observing different error messages for "bad input" vs "no
        # access".
        for required in tool.required_scopes:
            if not _scope_grants(ctx.user.scope, required):
                raise ToolPermissionError(f"missing required scope: {required}")

        try:
            payload = tool.input_schema.model_validate(dict(args))
        except ValidationError as exc:
            raise ToolValidationError(str(exc)) from exc

        try:
            result = await tool.handler(payload, ctx)
        except ToolError:
            raise
        except Exception as exc:  # noqa: BLE001 — we deliberately wrap unknowns
            log.warning(
                "mcp_tool_handler_failed",
                tool=tool.name,
                error_class=type(exc).__name__,
            )
            raise ToolHandlerError(f"tool {tool.name!r} failed ({type(exc).__name__})") from exc

        if not isinstance(result, tool.output_schema):
            try:
                result = tool.output_schema.model_validate(result)
            except ValidationError as exc:
                raise ToolValidationError(f"output schema mismatch for {tool.name!r}: {exc}") from exc

        if tool.audit_event is not None:
            await _emit_audit(tool, ctx, log)

        return result


# ── audit emit ─────────────────────────────────────────────────────


async def _emit_audit(
    tool: Tool,
    ctx: ToolContext,
    log: structlog.stdlib.BoundLogger,
) -> None:
    """Emit a generic ``AuditEventBase`` for ``tool`` on success.

    ``data`` deliberately carries only ``{"tool": tool.name}`` — the
    tool's input may contain secrets/tokens (audit_event matches a REST
    route, not a redaction policy) so we don't dump payload values into
    the audit log. Handlers that need richer audit detail can call
    :func:`safe_emit` themselves with a typed event class.
    """
    if ctx.audit_session is None:
        log.warning(
            "mcp_audit_skipped_no_session",
            tool=tool.name,
            audit_event=tool.audit_event,
        )
        return
    event = AuditEventBase(
        event_type=tool.audit_event,  # type: ignore[arg-type]
        actor=actor_from_user(ctx.user),
        tenant_id=ctx.user.active_tenant_id,
        data={"tool": tool.name},
    )
    await safe_emit(event, session=ctx.audit_session)


# ── auth resolver (3-way dispatch for MCP transports) ──────────────


async def resolve_tool_context(
    headers: Mapping[str, str],
    *,
    settings: Any,
    db: AsyncSession | None = None,
    audit_session: AsyncSession | None = None,
) -> ToolContext:
    """Resolve a :class:`ToolContext` from incoming request headers.

    Mirrors :func:`backend.src.core.auth._dispatch_token` but for MCP
    transports (where there is no FastAPI ``Request`` to lean on).

    Token routing:

      * ``bsv_admin_*`` → :func:`bsvibe_authz.verify_bootstrap_token`
      * ``bsv_sk_*``    → :func:`bsvibe_authz.verify_opaque_token`
      * other           → :func:`bsvibe_authz.verify_user_jwt`

    Auth failures collapse to :class:`ToolPermissionError` so the caller
    sees a uniform "missing/invalid token" message — never the raw
    bsvibe-authz exception text (which can mention internal config).
    """
    # case-insensitive header lookup
    auth = ""
    tenant_override: str | None = None
    for key, value in headers.items():
        lk = key.lower()
        if lk == "authorization":
            auth = value
        elif lk == "x-tenant-id":
            tenant_override = value
    if not auth.lower().startswith("bearer "):
        raise ToolPermissionError("missing bearer token")
    token = auth.split(" ", 1)[1].strip()
    if not token:
        raise ToolPermissionError("missing bearer token")

    az = _authz_settings()
    try:
        if token.startswith(BOOTSTRAP_TOKEN_PREFIX):
            user = verify_bootstrap_token(token, az)
        elif token.startswith(OPAQUE_TOKEN_PREFIX):
            client = _get_introspection_client()
            if client is None:
                raise AuthError("opaque token path is not configured")
            user = await verify_opaque_token(
                token,
                client,
                _get_introspection_cache(),
            )
        else:
            payload = verify_user_jwt(token, az)
            sub = payload.get("sub")
            if not isinstance(sub, str) or not sub:
                raise AuthError("user JWT missing sub")
            user = User(
                id=sub,
                email=payload.get("email"),
                scope=list(payload.get("scope") or []),
            )
    except AuthError as exc:
        # Don't echo the bsvibe-authz exception text — it can carry
        # config state. Tests that need to assert *which* failure
        # happened can monkeypatch the verify functions directly.
        logger.info("mcp_auth_rejected", reason=type(exc).__name__)
        raise ToolPermissionError("invalid bearer token") from exc

    # Bootstrap users have no ``active_tenant_id`` baked into the token —
    # the X-Tenant-Id header is the established BSNexus convention for
    # admin operators to pin a tenant per request (mirrors what
    # TenantMiddleware does for the REST surface). Honour it only when
    # the principal didn't already carry one to avoid privilege widening.
    if tenant_override and not user.active_tenant_id:
        user = user.model_copy(update={"active_tenant_id": tenant_override})

    return ToolContext(
        settings=settings,
        user=user,
        db=db,
        audit_session=audit_session,
        logger=logger,
    )


__all__ = [
    "Tool",
    "ToolContext",
    "ToolError",
    "ToolHandler",
    "ToolHandlerError",
    "ToolNotFoundError",
    "ToolPermissionError",
    "ToolRegistry",
    "ToolValidationError",
    "resolve_tool_context",
]
