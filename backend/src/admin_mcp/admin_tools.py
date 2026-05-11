"""BSNexus admin MCP tools — first-class definitions per CLI subcommand.

One tool per ``bsnexus`` CLI command. Handlers delegate to the injected
:data:`LoopbackCaller`, which drives the FastAPI app in-process so MCP
tools share the EXACT same request handlers the CLI hits over HTTP.

Naming follows ``bsnexus_<subapp>_<action>``. Required scopes mirror the
REST routes the equivalent CLI command authenticates against.

The admin surface is deliberately read-heavy + a few targeted writes
(create_project, resolve_decision, verify_deliverable). Write-heavy
flows (post_direction, ingest events) stay out of the admin MCP for
now — they have their own per-run authentication path
(:mod:`backend.src.mcp`) and the audit trail for those operations
demands a run-scoped HMAC token, not a user-scoped PAT.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, RootModel

from backend.src.admin_mcp.api import Tool, ToolContext, ToolError, ToolRegistry

LoopbackCaller = Callable[..., Awaitable[Any]]


# ---------------------------------------------------------------------------
# Output envelope — relaxed RootModel so the REST shape passes through.
# ---------------------------------------------------------------------------


class AdminToolResponse(RootModel[Any]):
    """Permissive output envelope — preserves the natural JSON shape each
    REST route returns without re-declaring response schemas here."""


def _ok(data: Any) -> AdminToolResponse:
    return AdminToolResponse(data)


# ---------------------------------------------------------------------------
# Input schemas
# ---------------------------------------------------------------------------


class _Empty(BaseModel):
    pass


class ProjectsListInput(BaseModel):
    limit: int = Field(50, ge=1, le=200)
    offset: int = Field(0, ge=0)


class ProjectsShowInput(BaseModel):
    project_id: UUID


class ProjectsCreateInput(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    settings: dict[str, Any] = Field(default_factory=dict)


class ProjectsUpdateInput(BaseModel):
    project_id: UUID
    name: str | None = None
    description: str | None = None
    settings: dict[str, Any] | None = None


class ProjectsDeleteInput(BaseModel):
    project_id: UUID


class RequestsListInput(BaseModel):
    project_id: UUID | None = None
    limit: int = Field(50, ge=1, le=200)


class DecisionsListInput(BaseModel):
    project_id: UUID | None = None
    blocking_only: bool = False


class DecisionsResolveInput(BaseModel):
    decision_id: UUID
    resolution: str = Field(min_length=1)


class DeliverablesListInput(BaseModel):
    project_id: UUID | None = None
    limit: int = Field(50, ge=1, le=200)


class DeliverablesVerifyInput(BaseModel):
    deliverable_id: UUID
    verified: bool = True
    note: str = ""


class BriefShowInput(BaseModel):
    project_id: UUID


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _h_projects_list(lb: LoopbackCaller):
    async def handler(args: ProjectsListInput, ctx: ToolContext) -> AdminToolResponse:
        return _ok(
            await lb(
                ctx,
                "GET",
                "/projects",
                params={"limit": args.limit, "offset": args.offset},
            )
        )

    return handler


def _h_projects_show(lb: LoopbackCaller):
    async def handler(args: ProjectsShowInput, ctx: ToolContext) -> AdminToolResponse:
        return _ok(await lb(ctx, "GET", f"/projects/{args.project_id}"))

    return handler


def _h_projects_create(lb: LoopbackCaller):
    async def handler(args: ProjectsCreateInput, ctx: ToolContext) -> AdminToolResponse:
        body = {
            "name": args.name,
            "description": args.description,
            "settings": dict(args.settings),
        }
        return _ok(await lb(ctx, "POST", "/projects", body=body))

    return handler


def _h_projects_update(lb: LoopbackCaller):
    async def handler(args: ProjectsUpdateInput, ctx: ToolContext) -> AdminToolResponse:
        body = args.model_dump(exclude_unset=True, exclude={"project_id"}, mode="json")
        if not body:
            raise ToolError(
                code="invalid_input",
                message="at least one field (name/description/settings) is required",
            )
        return _ok(await lb(ctx, "PATCH", f"/projects/{args.project_id}", body=body))

    return handler


def _h_projects_delete(lb: LoopbackCaller):
    async def handler(args: ProjectsDeleteInput, ctx: ToolContext) -> AdminToolResponse:
        return _ok(await lb(ctx, "DELETE", f"/projects/{args.project_id}"))

    return handler


def _h_requests_list(lb: LoopbackCaller):
    async def handler(args: RequestsListInput, ctx: ToolContext) -> AdminToolResponse:
        params: dict[str, Any] = {"limit": args.limit}
        if args.project_id is not None:
            params["project_id"] = str(args.project_id)
        return _ok(await lb(ctx, "GET", "/requests", params=params))

    return handler


def _h_decisions_list(lb: LoopbackCaller):
    async def handler(args: DecisionsListInput, ctx: ToolContext) -> AdminToolResponse:
        params: dict[str, Any] = {}
        if args.project_id is not None:
            params["project_id"] = str(args.project_id)
        if args.blocking_only:
            params["blocking_only"] = "true"
        return _ok(await lb(ctx, "GET", "/decisions", params=params or None))

    return handler


def _h_decisions_resolve(lb: LoopbackCaller):
    async def handler(args: DecisionsResolveInput, ctx: ToolContext) -> AdminToolResponse:
        body = {"resolution": args.resolution}
        return _ok(await lb(ctx, "POST", f"/decisions/{args.decision_id}/resolve", body=body))

    return handler


def _h_deliverables_list(lb: LoopbackCaller):
    async def handler(args: DeliverablesListInput, ctx: ToolContext) -> AdminToolResponse:
        params: dict[str, Any] = {"limit": args.limit}
        if args.project_id is not None:
            params["project_id"] = str(args.project_id)
        return _ok(await lb(ctx, "GET", "/deliverables", params=params))

    return handler


def _h_deliverables_verify(lb: LoopbackCaller):
    async def handler(args: DeliverablesVerifyInput, ctx: ToolContext) -> AdminToolResponse:
        body = {"verified": args.verified, "note": args.note}
        return _ok(
            await lb(
                ctx,
                "POST",
                f"/deliverables/{args.deliverable_id}/verify",
                body=body,
            )
        )

    return handler


def _h_brief_show(lb: LoopbackCaller):
    async def handler(args: BriefShowInput, ctx: ToolContext) -> AdminToolResponse:
        return _ok(await lb(ctx, "GET", "/brief", params={"project_id": str(args.project_id)}))

    return handler


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


EXPECTED_ADMIN_TOOL_NAMES: tuple[str, ...] = (
    "bsnexus_projects_list",
    "bsnexus_projects_show",
    "bsnexus_projects_create",
    "bsnexus_projects_update",
    "bsnexus_projects_delete",
    "bsnexus_requests_list",
    "bsnexus_decisions_list",
    "bsnexus_decisions_resolve",
    "bsnexus_deliverables_list",
    "bsnexus_deliverables_verify",
    "bsnexus_brief_show",
)


def register_admin_tools(registry: ToolRegistry, lb: LoopbackCaller) -> None:
    """Register all BSNexus admin tools onto ``registry``."""
    registry.register(
        Tool(
            name="bsnexus_projects_list",
            description="List BSNexus projects in the active tenant.",
            input_schema=ProjectsListInput,
            output_schema=AdminToolResponse,
            handler=_h_projects_list(lb),
            required_scopes=["nexus:projects:read"],
        )
    )
    registry.register(
        Tool(
            name="bsnexus_projects_show",
            description="Show a BSNexus project by id.",
            input_schema=ProjectsShowInput,
            output_schema=AdminToolResponse,
            handler=_h_projects_show(lb),
            required_scopes=["nexus:projects:read"],
        )
    )
    registry.register(
        Tool(
            name="bsnexus_projects_create",
            description="Create a new BSNexus project in the active tenant.",
            input_schema=ProjectsCreateInput,
            output_schema=AdminToolResponse,
            handler=_h_projects_create(lb),
            required_scopes=["nexus:projects:write"],
            audit_event="bsnexus.mcp.projects_create.invoked",
        )
    )
    registry.register(
        Tool(
            name="bsnexus_projects_update",
            description="Update a BSNexus project (name / description / settings).",
            input_schema=ProjectsUpdateInput,
            output_schema=AdminToolResponse,
            handler=_h_projects_update(lb),
            required_scopes=["nexus:projects:write"],
            audit_event="bsnexus.mcp.projects_update.invoked",
        )
    )
    registry.register(
        Tool(
            name="bsnexus_projects_delete",
            description="Archive (soft-delete) a BSNexus project.",
            input_schema=ProjectsDeleteInput,
            output_schema=AdminToolResponse,
            handler=_h_projects_delete(lb),
            required_scopes=["nexus:projects:write"],
            audit_event="bsnexus.mcp.projects_delete.invoked",
        )
    )
    registry.register(
        Tool(
            name="bsnexus_requests_list",
            description="List founder Requests (optionally filtered by project_id).",
            input_schema=RequestsListInput,
            output_schema=AdminToolResponse,
            handler=_h_requests_list(lb),
            required_scopes=["nexus:requests:read"],
        )
    )
    registry.register(
        Tool(
            name="bsnexus_decisions_list",
            description="List Decisions; --blocking-only filters to outstanding ones.",
            input_schema=DecisionsListInput,
            output_schema=AdminToolResponse,
            handler=_h_decisions_list(lb),
            required_scopes=["nexus:decisions:read"],
        )
    )
    registry.register(
        Tool(
            name="bsnexus_decisions_resolve",
            description="Lock in a Decision by submitting its resolution.",
            input_schema=DecisionsResolveInput,
            output_schema=AdminToolResponse,
            handler=_h_decisions_resolve(lb),
            required_scopes=["nexus:decisions:write"],
            audit_event="bsnexus.mcp.decisions_resolve.invoked",
        )
    )
    registry.register(
        Tool(
            name="bsnexus_deliverables_list",
            description="List Deliverables (optionally filtered by project_id).",
            input_schema=DeliverablesListInput,
            output_schema=AdminToolResponse,
            handler=_h_deliverables_list(lb),
            required_scopes=["nexus:deliverables:read"],
        )
    )
    registry.register(
        Tool(
            name="bsnexus_deliverables_verify",
            description="Mark a Deliverable as verified / unverified with an optional note.",
            input_schema=DeliverablesVerifyInput,
            output_schema=AdminToolResponse,
            handler=_h_deliverables_verify(lb),
            required_scopes=["nexus:deliverables:write"],
            audit_event="bsnexus.mcp.deliverables_verify.invoked",
        )
    )
    registry.register(
        Tool(
            name="bsnexus_brief_show",
            description="Get the founder brief snapshot for a project.",
            input_schema=BriefShowInput,
            output_schema=AdminToolResponse,
            handler=_h_brief_show(lb),
            required_scopes=["nexus:brief:read"],
        )
    )


__all__ = [
    "AdminToolResponse",
    "EXPECTED_ADMIN_TOOL_NAMES",
    "LoopbackCaller",
    "register_admin_tools",
]
