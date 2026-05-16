"""First-class admin MCP API for BSNexus (Round 4 production-ready).

Mirrors the shape used by BSGateway / BSage / BSupervisor MCP modules so
the four products present a uniform first-class Tool primitive to MCP
clients (Claude Code, IDE plugins).

Distinct from :mod:`backend.src.mcp` — that module hosts the existing
per-run HMAC-token MCP server used by BSGateway-spawned workers. This
module is the **external admin surface**: tools wired to the same REST
routes the ``bsnexus`` CLI hits, authenticated by the user's PAT JWT.

Contract:

* ``Tool`` is a typed primitive with explicit Pydantic v2 input/output
  schemas, an async handler, a required permission, and an optional
  audit event literal.
* ``ToolRegistry`` is the single dispatcher: validate input, enforce
  ``required_permission`` via the shared ``bsvibe_authz`` OpenFGA
  ``check_tenant_permission`` against the caller's
  :class:`bsvibe_authz.User`, run the handler, validate output, emit
  ``audit_event`` on success (best-effort).

Tier 5 Phase 3a — MCP tool authorization moved from scope-claim checks
(``_scope_grants``) to the same OpenFGA per-resource model REST routes
use. ``required_permission`` is a ``<product>.<resource>.<action>`` dot
string (e.g. ``bsnexus.projects.read``); each maps to a row of
``packages/bsvibe-authz/schema/permission_matrix.yaml``. The check is
permissive when OpenFGA is unconfigured (dev/test/prod today), matching
the REST ``require_permission`` posture.
* Errors leave the dispatcher as a typed :class:`ToolError` with a
  stable ``code`` literal (``invalid_input``, ``permission_denied``,
  ``tool_not_found``, ``invalid_output``, ``unauthenticated``).
* Handler-raised :class:`ToolError` propagates unchanged so domain code
  can surface application errors with their own codes.

Round 4 lessons folded in:
* F17 — `IntrospectionCache` is upstream-fixed in bsvibe-authz 0.8+.
* F20 — loopback HTTP errors translate to typed ToolError without
  internal URL leaks (see :mod:`backend.src.admin_mcp.lifespan`).
* F22 — call_tool wraps results in TextContent for wire format
  (see :mod:`backend.src.admin_mcp.server`).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import structlog
from bsvibe_authz import (
    IntrospectionClient,
    Settings,
    User,
    get_openfga_client,
    get_permission_cache,
)
from bsvibe_authz.cache import IntrospectionCache, PermissionCache
from bsvibe_authz.deps import FGAClientProtocol, check_tenant_permission
from bsvibe_authz.deps import get_current_user as _authz_get_current_user
from fastapi import HTTPException
from pydantic import BaseModel, ValidationError

logger = structlog.get_logger(__name__)


def _permissive_authz_settings() -> Settings:
    """A minimal ``bsvibe_authz.Settings`` with OpenFGA unconfigured.

    Fallback for dispatch paths that did not receive an ``authz_settings``
    on the :class:`ToolContext` (e.g. unit tests). With ``openfga_api_url``
    empty, :func:`check_tenant_permission` returns ``True`` permissively —
    the authenticated caller passes, mirroring the REST permissive mode.
    """
    return Settings(openfga_api_url="", openfga_store_id="", openfga_auth_model_id="")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ToolError(Exception):
    """Typed error surface for MCP tool dispatch.

    The ``code`` literal is part of the public contract — tests assert
    against it and clients route on it. Built-in dispatcher codes:

    * ``tool_not_found`` — registry miss
    * ``invalid_input`` — input did not validate against ``input_schema``
    * ``invalid_output`` — handler returned a value that failed
      ``output_schema`` validation
    * ``permission_denied`` — caller does not hold a required scope
    * ``unauthenticated`` — auth resolver rejected the request

    Handlers MAY raise :class:`ToolError` with a domain-specific code
    (``not_found``, ``conflict``); the dispatcher leaves the code
    untouched.
    """

    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Tool primitive + context
# ---------------------------------------------------------------------------


@dataclass
class ToolContext:
    """Resolved per-call execution context.

    Mirrors the FastAPI request-scope context REST handlers see. ``app_state``
    is the live ``FastAPI.state`` so handlers that need DB pools / cache
    managers / outbox sessions can pluck them off.

    ``authz_settings`` / ``fga`` / ``cache`` carry the inputs the Tier-5
    OpenFGA tool-authorization check needs. They are populated by
    :func:`resolve_tool_context`; when absent (e.g. unit tests that build
    a ``ToolContext`` directly) the dispatcher falls back to a permissive
    no-OpenFGA Settings so authenticated callers pass — the same posture
    as the REST ``require_permission`` dependency.
    """

    user: User
    app_state: Any | None = None
    authz_settings: Settings | None = None
    fga: FGAClientProtocol | None = None
    cache: PermissionCache | None = None


_HandlerType = Callable[[BaseModel, ToolContext], Awaitable[BaseModel]]


@dataclass
class Tool:
    """First-class MCP tool definition (mirror of a REST route).

    ``required_permission`` is a ``<product>.<resource>.<action>`` dot
    string enforced via the shared OpenFGA ``check_tenant_permission``
    (Tier 5 Phase 3a). ``None`` means the tool is unguarded beyond
    authentication.
    """

    name: str
    description: str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    handler: _HandlerType
    required_permission: str | None = None
    audit_event: str | None = None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class ToolRegistry:
    """In-memory catalog of first-class :class:`Tool` definitions."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def list_tools(self) -> list[Any]:
        """Return MCP ``Tool`` listings derived from each registered tool's
        Pydantic input schema."""
        from mcp.types import Tool as McpTool

        out: list[Any] = []
        for tool in self._tools.values():
            out.append(
                McpTool(
                    name=tool.name,
                    description=tool.description,
                    inputSchema=tool.input_schema.model_json_schema(),
                )
            )
        return out

    async def call_tool(
        self,
        name: str,
        args: dict[str, Any],
        ctx: ToolContext,
    ) -> dict[str, Any]:
        """Validate → authorise → execute → validate output → audit emit."""
        tool = self.get(name)
        if tool is None:
            raise ToolError(code="tool_not_found", message=f"unknown tool: {name}")

        # 1. enforce permission via the shared OpenFGA model (Tier 5 Phase
        #    3a). MCP tool authz now uses the SAME per-resource model REST
        #    routes enforce — `check_tenant_permission` is permissive when
        #    OpenFGA is unconfigured (dev/test/prod today).
        if tool.required_permission is not None:
            authz_settings = ctx.authz_settings or _permissive_authz_settings()
            fga = ctx.fga or get_openfga_client(authz_settings)
            cache = ctx.cache or get_permission_cache(authz_settings)
            allowed = await check_tenant_permission(
                ctx.user,
                tool.required_permission,
                fga=fga,
                cache=cache,
                settings=authz_settings,
            )
            if not allowed:
                logger.info(
                    "mcp_tool_denied",
                    tool=tool.name,
                    user_id=ctx.user.id,
                    required_permission=tool.required_permission,
                )
                raise ToolError(
                    code="permission_denied",
                    message=f"permission denied: {tool.required_permission}",
                )

        # 2. validate input
        try:
            parsed = tool.input_schema.model_validate(args)
        except ValidationError as exc:
            raise ToolError(code="invalid_input", message=str(exc.errors())) from exc

        # 3. invoke handler
        try:
            result = await tool.handler(parsed, ctx)
        except ToolError:
            raise
        except Exception as exc:
            # F21 mirror: expose class name, redact message
            logger.exception(
                "mcp_tool_handler_failed",
                tool=name,
                error_type=type(exc).__name__,
            )
            raise ToolError(
                code="internal_error",
                message=f"tool {name!r} failed: {type(exc).__name__}",
            ) from exc

        # 4. validate output
        if not isinstance(result, BaseModel):
            try:
                result = tool.output_schema.model_validate(result)
            except ValidationError as exc:
                raise ToolError(
                    code="invalid_output",
                    message=str(exc.errors()),
                ) from exc

        return result.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Auth resolver
# ---------------------------------------------------------------------------


async def resolve_tool_context(
    headers: Mapping[str, str],
    *,
    app_state: Any | None = None,
    settings: Settings,
    introspection_client: IntrospectionClient | None,
    introspection_cache: IntrospectionCache,
) -> ToolContext:
    """Authenticate an MCP request from raw headers → :class:`ToolContext`.

    Delegates to :func:`bsvibe_authz.deps.get_current_user` for the full
    bootstrap → opaque → JWT → PAT-JWT introspection-fallback dispatch.

    The resolved ``settings`` plus the process-wide OpenFGA client and
    permission cache are threaded onto the :class:`ToolContext` so the
    dispatcher's Tier-5 ``check_tenant_permission`` enforces the same
    OpenFGA model REST routes use (permissive when OpenFGA is
    unconfigured).
    """
    auth_header = headers.get("authorization") or headers.get("Authorization")
    try:
        user = await _authz_get_current_user(
            authorization=auth_header,
            settings=settings,
            introspection_client=introspection_client,
            introspection_cache=introspection_cache,
        )
    except HTTPException as exc:
        raise ToolError(code="unauthenticated", message=str(exc.detail)) from exc
    return ToolContext(
        user=user,
        app_state=app_state,
        authz_settings=settings,
        fga=get_openfga_client(settings),
        cache=get_permission_cache(settings),
    )


__all__ = [
    "Tool",
    "ToolContext",
    "ToolError",
    "ToolRegistry",
    "resolve_tool_context",
]
