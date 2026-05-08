"""Admin MCP tools — first-class :class:`Tool` definitions (TASK-004).

Phase 7 architectural decision (2026-05-08): MCP is a first-class API
surface. Admin tools mirror the BSNexus CLI sub-apps but are NOT
auto-derived from Typer commands — every tool declares Pydantic
input/output schemas, an async handler that calls the same
service-layer helpers the REST routers use, and the
``bsnexus:<resource>:<action>`` scope an admin bearer token must carry.

Naming convention: ``bsnexus_<subapp>_<action>`` — see
``.agent/mcp-inventory.md`` §3 for the full catalog (20 tools across 6
sub-apps).

Auth model contrasts with :mod:`backend.src.mcp.domain_tools`:

* Domain tools authenticate via run-scoped HMAC token and require the
  single sentinel scope ``DOMAIN_RUN_SCOPE`` — they're called by run
  workers, not humans.
* Admin tools authenticate via bootstrap / opaque / JWT bearer (handled
  by :func:`backend.src.mcp.api.resolve_tool_context`) and require
  proper ``bsnexus:*`` scopes — they're called by operators / scripts.

Tools that don't have a backing REST endpoint today (``decisions
unlock``, ``deliverables attach``, ``events list``) raise
:class:`ToolHandlerError` rather than silently no-op'ing — the catalog
exposes the surface for parity with the CLI, but the handler is honest
about not being supported.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.api.decisions import _apply_decision_resolution_with_audit
from backend.src.api.projects import _persist_project_with_audit
from backend.src.config import settings as app_settings
from backend.src.core.encryption import EncryptionManager
from backend.src.mcp.api import Tool, ToolContext, ToolHandlerError
from backend.src.models import (
    Decision,
    Deliverable,
    Project,
    Request,
    RequestStatus,
    TenantIntegrationConfig,
)
from backend.src.models.tenant_integration_config import IntegrationProvider
from backend.src.schemas import (
    DecisionResolve,
    DecisionResponse,
    DeliverableResponse,
    IntegrationConfigList,
    IntegrationConfigResponse,
    ProjectCreate,
    ProjectResponse,
    RequestResponse,
)
from backend.src.schemas.integration import redacted

logger = structlog.get_logger(__name__)


# ── scope constants ───────────────────────────────────────────────


_SCOPE_PROJECTS_READ = "bsnexus:projects:read"
_SCOPE_PROJECTS_WRITE = "bsnexus:projects:write"
_SCOPE_REQUESTS_READ = "bsnexus:requests:read"
_SCOPE_REQUESTS_WRITE = "bsnexus:requests:write"
_SCOPE_DECISIONS_READ = "bsnexus:decisions:read"
_SCOPE_DECISIONS_WRITE = "bsnexus:decisions:write"
_SCOPE_DELIVERABLES_READ = "bsnexus:deliverables:read"
_SCOPE_DELIVERABLES_WRITE = "bsnexus:deliverables:write"
_SCOPE_EVENTS_READ = "bsnexus:events:read"
_SCOPE_INTEGRATIONS_READ = "bsnexus:integrations:read"
_SCOPE_INTEGRATIONS_WRITE = "bsnexus:integrations:write"


# ── shared helpers ────────────────────────────────────────────────


def _require_tenant(ctx: ToolContext) -> uuid.UUID:
    """Pull the active tenant out of the resolved auth principal.

    Admin tools are tenant-scoped end-to-end. The bootstrap path stuffs
    ``active_tenant_id`` into the :class:`User`; for the JWT path it
    flows from the ``tenant_id`` claim. A missing tenant is a
    :class:`ToolHandlerError` rather than a 500.
    """
    tid = ctx.user.active_tenant_id
    if not tid:
        raise ToolHandlerError("missing tenant in caller context")
    try:
        return uuid.UUID(str(tid))
    except (TypeError, ValueError) as exc:
        raise ToolHandlerError("invalid tenant in caller context") from exc


def _require_db(ctx: ToolContext) -> AsyncSession:
    if ctx.db is None:
        raise ToolHandlerError("missing database session in caller context")
    return ctx.db


async def _get_project(db: AsyncSession, tenant_id: uuid.UUID, project_id: uuid.UUID) -> Project:
    stmt = select(Project).where(Project.id == project_id, Project.tenant_id == tenant_id)
    project = (await db.execute(stmt)).scalar_one_or_none()
    if project is None:
        raise ToolHandlerError("project not found")
    return project


# ── Projects ──────────────────────────────────────────────────────


class ProjectsListInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectsListOutput(BaseModel):
    items: list[ProjectResponse] = Field(default_factory=list)


async def _projects_list_handler(_payload: ProjectsListInput, ctx: ToolContext) -> ProjectsListOutput:
    db = _require_db(ctx)
    tenant_id = _require_tenant(ctx)
    rows = (
        await db.execute(select(Project).where(Project.tenant_id == tenant_id).order_by(Project.created_at.desc()))
    ).scalars()
    return ProjectsListOutput(items=[ProjectResponse.model_validate(r, from_attributes=True) for r in rows])


PROJECTS_LIST_TOOL = Tool(
    name="bsnexus_projects_list",
    description="List projects in the active tenant.",
    input_schema=ProjectsListInput,
    output_schema=ProjectsListOutput,
    handler=_projects_list_handler,
    required_scopes=[_SCOPE_PROJECTS_READ],
)


class ProjectsShowInput(BaseModel):
    project_id: str = Field(..., min_length=1)
    model_config = ConfigDict(extra="forbid")


async def _projects_show_handler(payload: ProjectsShowInput, ctx: ToolContext) -> ProjectResponse:
    db = _require_db(ctx)
    tenant_id = _require_tenant(ctx)
    project = await _get_project(db, tenant_id, uuid.UUID(payload.project_id))
    return ProjectResponse.model_validate(project, from_attributes=True)


PROJECTS_SHOW_TOOL = Tool(
    name="bsnexus_projects_show",
    description="Show a single project by id.",
    input_schema=ProjectsShowInput,
    output_schema=ProjectResponse,
    handler=_projects_show_handler,
    required_scopes=[_SCOPE_PROJECTS_READ],
)


class ProjectsCreateInput(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field("", max_length=10_000)
    bsage_workspace_id: str | None = None
    bsupervisor_policy_id: str | None = None

    model_config = ConfigDict(extra="forbid")


async def _projects_create_handler(payload: ProjectsCreateInput, ctx: ToolContext) -> ProjectResponse:
    """Delegate to :func:`_persist_project_with_audit` so MCP and REST
    write the same outbox row (``nexus.project.created``)."""
    db = _require_db(ctx)
    tenant_id = _require_tenant(ctx)
    project = await _persist_project_with_audit(
        payload=ProjectCreate(
            name=payload.name,
            description=payload.description,
            bsage_workspace_id=payload.bsage_workspace_id,
            bsupervisor_policy_id=payload.bsupervisor_policy_id,
        ),
        user=ctx.user,
        tenant_id=tenant_id,
        session=db,
    )
    await db.commit()
    await db.refresh(project)
    return ProjectResponse.model_validate(project, from_attributes=True)


PROJECTS_CREATE_TOOL = Tool(
    name="bsnexus_projects_create",
    description="Create a new project. Audit emits via the REST helper, not the dispatcher.",
    input_schema=ProjectsCreateInput,
    output_schema=ProjectResponse,
    handler=_projects_create_handler,
    required_scopes=[_SCOPE_PROJECTS_WRITE],
)


class ProjectsArchiveInput(BaseModel):
    project_id: str = Field(..., min_length=1)
    model_config = ConfigDict(extra="forbid")


class ProjectsArchiveOutput(BaseModel):
    archived: str


async def _projects_archive_handler(payload: ProjectsArchiveInput, ctx: ToolContext) -> ProjectsArchiveOutput:
    db = _require_db(ctx)
    tenant_id = _require_tenant(ctx)
    project = await _get_project(db, tenant_id, uuid.UUID(payload.project_id))
    await db.delete(project)
    await db.commit()
    return ProjectsArchiveOutput(archived=str(project.id))


PROJECTS_ARCHIVE_TOOL = Tool(
    name="bsnexus_projects_archive",
    description="Archive (delete) a project.",
    input_schema=ProjectsArchiveInput,
    output_schema=ProjectsArchiveOutput,
    handler=_projects_archive_handler,
    required_scopes=[_SCOPE_PROJECTS_WRITE],
    audit_event="nexus.project.archived",
)


# ── Requests ──────────────────────────────────────────────────────


class RequestsListInput(BaseModel):
    project_id: str | None = None
    limit: int = Field(50, ge=1, le=200)
    model_config = ConfigDict(extra="forbid")


class RequestsListOutput(BaseModel):
    items: list[RequestResponse] = Field(default_factory=list)


async def _requests_list_handler(payload: RequestsListInput, ctx: ToolContext) -> RequestsListOutput:
    db = _require_db(ctx)
    tenant_id = _require_tenant(ctx)
    stmt = select(Request).where(Request.tenant_id == tenant_id)
    if payload.project_id is not None:
        stmt = stmt.where(Request.project_id == uuid.UUID(payload.project_id))
    stmt = stmt.order_by(Request.created_at.desc()).limit(payload.limit)
    rows = (await db.execute(stmt)).scalars()
    return RequestsListOutput(items=[RequestResponse.model_validate(r, from_attributes=True) for r in rows])


REQUESTS_LIST_TOOL = Tool(
    name="bsnexus_requests_list",
    description="List founder requests. Omit project_id for cross-project tenant view.",
    input_schema=RequestsListInput,
    output_schema=RequestsListOutput,
    handler=_requests_list_handler,
    required_scopes=[_SCOPE_REQUESTS_READ],
)


class RequestsShowInput(BaseModel):
    request_id: str = Field(..., min_length=1)
    model_config = ConfigDict(extra="forbid")


async def _requests_show_handler(payload: RequestsShowInput, ctx: ToolContext) -> RequestResponse:
    db = _require_db(ctx)
    tenant_id = _require_tenant(ctx)
    stmt = select(Request).where(
        Request.id == uuid.UUID(payload.request_id),
        Request.tenant_id == tenant_id,
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise ToolHandlerError("request not found")
    return RequestResponse.model_validate(row, from_attributes=True)


REQUESTS_SHOW_TOOL = Tool(
    name="bsnexus_requests_show",
    description="Show a single request by id.",
    input_schema=RequestsShowInput,
    output_schema=RequestResponse,
    handler=_requests_show_handler,
    required_scopes=[_SCOPE_REQUESTS_READ],
)


class RequestsCreateInput(BaseModel):
    project_id: str = Field(..., min_length=1)
    intent_summary: str = Field(..., min_length=1, max_length=10_000)
    model_config = ConfigDict(extra="forbid")


async def _requests_create_handler(payload: RequestsCreateInput, ctx: ToolContext) -> RequestResponse:
    """Direct Request insert. The full ``POST /messages`` path runs an
    LLM-driven dispatch we don't want from an admin tool — operators
    that need a real conversation should use the chat surface."""
    db = _require_db(ctx)
    tenant_id = _require_tenant(ctx)
    project_id = uuid.UUID(payload.project_id)
    await _get_project(db, tenant_id, project_id)
    row = Request(
        tenant_id=tenant_id,
        project_id=project_id,
        intent_summary=payload.intent_summary[:240],
        status=RequestStatus.open,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return RequestResponse.model_validate(row, from_attributes=True)


REQUESTS_CREATE_TOOL = Tool(
    name="bsnexus_requests_create",
    description="Open a new Request directly (admin path; bypasses chat dispatch).",
    input_schema=RequestsCreateInput,
    output_schema=RequestResponse,
    handler=_requests_create_handler,
    required_scopes=[_SCOPE_REQUESTS_WRITE],
    audit_event="nexus.request.created",
)


class RequestsUpdateInput(BaseModel):
    request_id: str = Field(..., min_length=1)
    status: RequestStatus | None = None
    intent_summary: str | None = Field(None, max_length=10_000)
    model_config = ConfigDict(extra="forbid")


async def _requests_update_handler(payload: RequestsUpdateInput, ctx: ToolContext) -> RequestResponse:
    db = _require_db(ctx)
    tenant_id = _require_tenant(ctx)
    stmt = select(Request).where(
        Request.id == uuid.UUID(payload.request_id),
        Request.tenant_id == tenant_id,
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise ToolHandlerError("request not found")
    if payload.status is not None:
        row.status = payload.status
    if payload.intent_summary is not None:
        row.intent_summary = payload.intent_summary[:240]
    await db.commit()
    await db.refresh(row)
    return RequestResponse.model_validate(row, from_attributes=True)


REQUESTS_UPDATE_TOOL = Tool(
    name="bsnexus_requests_update",
    description="Update Request status / intent_summary (admin path).",
    input_schema=RequestsUpdateInput,
    output_schema=RequestResponse,
    handler=_requests_update_handler,
    required_scopes=[_SCOPE_REQUESTS_WRITE],
    audit_event="nexus.request.updated",
)


# ── Decisions ─────────────────────────────────────────────────────


class DecisionsListInput(BaseModel):
    project_id: str | None = None
    blocking_only: bool = False
    resolved: bool | None = None
    limit: int = Field(50, ge=1, le=200)
    model_config = ConfigDict(extra="forbid")


class DecisionsListOutput(BaseModel):
    items: list[DecisionResponse] = Field(default_factory=list)


async def _decisions_list_handler(payload: DecisionsListInput, ctx: ToolContext) -> DecisionsListOutput:
    db = _require_db(ctx)
    tenant_id = _require_tenant(ctx)
    stmt = select(Decision).where(Decision.tenant_id == tenant_id)
    if payload.project_id is not None:
        stmt = stmt.where(Decision.project_id == uuid.UUID(payload.project_id))
    if payload.blocking_only:
        stmt = stmt.where(Decision.blocking.is_(True), Decision.resolved_at.is_(None))
    elif payload.resolved is True:
        stmt = stmt.where(Decision.resolved_at.is_not(None))
    elif payload.resolved is False:
        stmt = stmt.where(Decision.resolved_at.is_(None))
    stmt = stmt.order_by(
        Decision.resolved_at.is_(None).desc(),
        Decision.blocking.desc(),
        Decision.created_at.desc(),
    ).limit(payload.limit)
    rows = (await db.execute(stmt)).scalars()
    return DecisionsListOutput(items=[DecisionResponse.model_validate(r, from_attributes=True) for r in rows])


DECISIONS_LIST_TOOL = Tool(
    name="bsnexus_decisions_list",
    description="List decisions in the inbox. Open-first → blocking-first → newest.",
    input_schema=DecisionsListInput,
    output_schema=DecisionsListOutput,
    handler=_decisions_list_handler,
    required_scopes=[_SCOPE_DECISIONS_READ],
)


class DecisionsShowInput(BaseModel):
    decision_id: str = Field(..., min_length=1)
    model_config = ConfigDict(extra="forbid")


async def _decisions_show_handler(payload: DecisionsShowInput, ctx: ToolContext) -> DecisionResponse:
    db = _require_db(ctx)
    tenant_id = _require_tenant(ctx)
    stmt = select(Decision).where(
        Decision.id == uuid.UUID(payload.decision_id),
        Decision.tenant_id == tenant_id,
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise ToolHandlerError("decision not found")
    return DecisionResponse.model_validate(row, from_attributes=True)


DECISIONS_SHOW_TOOL = Tool(
    name="bsnexus_decisions_show",
    description="Show a single decision by id.",
    input_schema=DecisionsShowInput,
    output_schema=DecisionResponse,
    handler=_decisions_show_handler,
    required_scopes=[_SCOPE_DECISIONS_READ],
)


class DecisionsLockInput(BaseModel):
    decision_id: str = Field(..., min_length=1)
    resolution: str = Field(..., min_length=1)
    resolved_by: str | None = None
    model_config = ConfigDict(extra="forbid")


async def _decisions_lock_handler(payload: DecisionsLockInput, ctx: ToolContext) -> DecisionResponse:
    """Resolve a decision by delegating to the same audited helper the
    REST router uses, so the ``nexus.decision.resolved`` outbox row
    matches the REST contract.

    Inner SSE / knowledge-record side effects are presentation-layer
    concerns the MCP path leaves alone — agents driving MCP shouldn't
    pre-empt founder-facing UI updates.
    """
    db = _require_db(ctx)
    tenant_id = _require_tenant(ctx)
    stmt = select(Decision).where(
        Decision.id == uuid.UUID(payload.decision_id),
        Decision.tenant_id == tenant_id,
    )
    decision = (await db.execute(stmt)).scalar_one_or_none()
    if decision is None:
        raise ToolHandlerError("decision not found")

    decision = await _apply_decision_resolution_with_audit(
        decision=decision,
        payload=DecisionResolve(resolution=payload.resolution, resolved_by=payload.resolved_by),
        user=ctx.user,
        tenant_id=tenant_id,
        session=db,
    )
    await db.commit()
    await db.refresh(decision)
    return DecisionResponse.model_validate(decision, from_attributes=True)


DECISIONS_LOCK_TOOL = Tool(
    name="bsnexus_decisions_lock",
    description="Resolve (lock) a decision. Audit emits via the REST helper.",
    input_schema=DecisionsLockInput,
    output_schema=DecisionResponse,
    handler=_decisions_lock_handler,
    required_scopes=[_SCOPE_DECISIONS_WRITE],
)


class DecisionsUnlockInput(BaseModel):
    decision_id: str = Field(..., min_length=1)
    model_config = ConfigDict(extra="forbid")


class _NotSupportedOutput(BaseModel):
    """Schema slot for ``raise``-only handlers — never returned, but
    kept Pydantic-typed so :meth:`Tool.list_tools` can still emit a
    valid output JSON schema."""

    detail: str = ""


async def _decisions_unlock_handler(_payload: DecisionsUnlockInput, _ctx: ToolContext) -> _NotSupportedOutput:
    raise ToolHandlerError("decisions unlock is not supported — the backend exposes no reopen endpoint")


DECISIONS_UNLOCK_TOOL = Tool(
    name="bsnexus_decisions_unlock",
    description="Reopen a decision. NOT SUPPORTED — no backing REST endpoint.",
    input_schema=DecisionsUnlockInput,
    output_schema=_NotSupportedOutput,
    handler=_decisions_unlock_handler,
    required_scopes=[_SCOPE_DECISIONS_WRITE],
)


# ── Deliverables ──────────────────────────────────────────────────


class DeliverablesListInput(BaseModel):
    project_id: str | None = None
    limit: int = Field(50, ge=1, le=200)
    model_config = ConfigDict(extra="forbid")


class DeliverablesListOutput(BaseModel):
    items: list[DeliverableResponse] = Field(default_factory=list)


async def _deliverables_list_handler(payload: DeliverablesListInput, ctx: ToolContext) -> DeliverablesListOutput:
    db = _require_db(ctx)
    tenant_id = _require_tenant(ctx)
    stmt = select(Deliverable).where(Deliverable.tenant_id == tenant_id)
    if payload.project_id is not None:
        stmt = stmt.where(Deliverable.project_id == uuid.UUID(payload.project_id))
    stmt = stmt.order_by(Deliverable.created_at.desc()).limit(payload.limit)
    rows = (await db.execute(stmt)).scalars()
    return DeliverablesListOutput(items=[DeliverableResponse.model_validate(r, from_attributes=True) for r in rows])


DELIVERABLES_LIST_TOOL = Tool(
    name="bsnexus_deliverables_list",
    description="List deliverables. Omit project_id for cross-project tenant view.",
    input_schema=DeliverablesListInput,
    output_schema=DeliverablesListOutput,
    handler=_deliverables_list_handler,
    required_scopes=[_SCOPE_DELIVERABLES_READ],
)


class DeliverablesShowInput(BaseModel):
    deliverable_id: str = Field(..., min_length=1)
    model_config = ConfigDict(extra="forbid")


async def _deliverables_show_handler(payload: DeliverablesShowInput, ctx: ToolContext) -> DeliverableResponse:
    db = _require_db(ctx)
    tenant_id = _require_tenant(ctx)
    stmt = select(Deliverable).where(
        Deliverable.id == uuid.UUID(payload.deliverable_id),
        Deliverable.tenant_id == tenant_id,
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise ToolHandlerError("deliverable not found")
    return DeliverableResponse.model_validate(row, from_attributes=True)


DELIVERABLES_SHOW_TOOL = Tool(
    name="bsnexus_deliverables_show",
    description="Show a single deliverable by id.",
    input_schema=DeliverablesShowInput,
    output_schema=DeliverableResponse,
    handler=_deliverables_show_handler,
    required_scopes=[_SCOPE_DELIVERABLES_READ],
)


class DeliverablesAttachInput(BaseModel):
    deliverable_id: str = Field(..., min_length=1)
    path: str = Field(..., min_length=1)
    model_config = ConfigDict(extra="forbid")


async def _deliverables_attach_handler(_payload: DeliverablesAttachInput, _ctx: ToolContext) -> _NotSupportedOutput:
    raise ToolHandlerError("deliverables attach is not supported via MCP — use the REST upload path")


DELIVERABLES_ATTACH_TOOL = Tool(
    name="bsnexus_deliverables_attach",
    description="Attach a file to a deliverable. NOT SUPPORTED — use REST upload.",
    input_schema=DeliverablesAttachInput,
    output_schema=_NotSupportedOutput,
    handler=_deliverables_attach_handler,
    required_scopes=[_SCOPE_DELIVERABLES_WRITE],
)


# ── Events ────────────────────────────────────────────────────────


class EventsListInput(BaseModel):
    project_id: str = Field(..., min_length=1)
    limit: int = Field(50, ge=1, le=200)
    model_config = ConfigDict(extra="forbid")


async def _events_list_handler(_payload: EventsListInput, _ctx: ToolContext) -> _NotSupportedOutput:
    raise ToolHandlerError("events list is a streaming concern — connect to GET /api/v1/events?project_id=… (SSE)")


EVENTS_LIST_TOOL = Tool(
    name="bsnexus_events_list",
    description="Tail SSE events. NOT SUPPORTED — connect to /api/v1/events directly.",
    input_schema=EventsListInput,
    output_schema=_NotSupportedOutput,
    handler=_events_list_handler,
    required_scopes=[_SCOPE_EVENTS_READ],
)


# ── Integrations ──────────────────────────────────────────────────


def _encryption() -> EncryptionManager:
    return EncryptionManager(app_settings.encryption_key)


class IntegrationsListInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


async def _integrations_list_handler(_payload: IntegrationsListInput, ctx: ToolContext) -> IntegrationConfigList:
    db = _require_db(ctx)
    tenant_id = _require_tenant(ctx)
    rows = {
        row.provider: row
        for row in (
            await db.execute(select(TenantIntegrationConfig).where(TenantIntegrationConfig.tenant_id == tenant_id))
        ).scalars()
    }
    return IntegrationConfigList(
        bsage=redacted(IntegrationProvider.bsage, rows.get(IntegrationProvider.bsage)),
        bsupervisor=redacted(IntegrationProvider.bsupervisor, rows.get(IntegrationProvider.bsupervisor)),
    )


INTEGRATIONS_LIST_TOOL = Tool(
    name="bsnexus_integrations_list",
    description="List per-tenant integration configs (api_key never on the wire).",
    input_schema=IntegrationsListInput,
    output_schema=IntegrationConfigList,
    handler=_integrations_list_handler,
    required_scopes=[_SCOPE_INTEGRATIONS_READ],
)


class IntegrationsAddInput(BaseModel):
    """Input for upsert. ``api_key`` lives only in this in-memory model;
    it never lands on a row verbatim — the handler encrypts via
    :class:`EncryptionManager` before persisting."""

    provider: IntegrationProvider
    base_url: str | None = Field(None, max_length=500)
    api_key: str | None = Field(None, max_length=4_096)
    enabled: bool | None = None
    extra_config: dict[str, Any] | None = None

    model_config = ConfigDict(extra="forbid")


async def _integrations_upsert(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    provider: IntegrationProvider,
    *,
    base_url: str | None,
    api_key: str | None,
    enabled: bool | None,
    extra_config: dict[str, Any] | None,
) -> TenantIntegrationConfig:
    stmt = select(TenantIntegrationConfig).where(
        TenantIntegrationConfig.tenant_id == tenant_id,
        TenantIntegrationConfig.provider == provider,
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        row = TenantIntegrationConfig(tenant_id=tenant_id, provider=provider)
        db.add(row)
    if enabled is not None:
        row.enabled = bool(enabled)
    if base_url is not None:
        row.base_url = base_url or None
    if extra_config is not None:
        row.extra_config = extra_config or {}
    if api_key is not None:
        row.api_key_encrypted = _encryption().encrypt_value(api_key) if api_key else None
    await db.commit()
    await db.refresh(row)
    return row


async def _integrations_add_handler(payload: IntegrationsAddInput, ctx: ToolContext) -> IntegrationConfigResponse:
    db = _require_db(ctx)
    tenant_id = _require_tenant(ctx)
    row = await _integrations_upsert(
        db,
        tenant_id,
        payload.provider,
        base_url=payload.base_url,
        api_key=payload.api_key,
        enabled=payload.enabled,
        extra_config=payload.extra_config,
    )
    return redacted(payload.provider, row)


INTEGRATIONS_ADD_TOOL = Tool(
    name="bsnexus_integrations_add",
    description="Upsert an integration config. api_key is encrypted at rest before insert.",
    input_schema=IntegrationsAddInput,
    output_schema=IntegrationConfigResponse,
    handler=_integrations_add_handler,
    required_scopes=[_SCOPE_INTEGRATIONS_WRITE],
    audit_event="nexus.integration.updated",
)


class IntegrationsRemoveInput(BaseModel):
    provider: IntegrationProvider
    model_config = ConfigDict(extra="forbid")


async def _integrations_remove_handler(payload: IntegrationsRemoveInput, ctx: ToolContext) -> IntegrationConfigResponse:
    """Disable + clear key (the row stays so audit history survives)."""
    db = _require_db(ctx)
    tenant_id = _require_tenant(ctx)
    row = await _integrations_upsert(
        db,
        tenant_id,
        payload.provider,
        base_url=None,
        api_key="",
        enabled=False,
        extra_config=None,
    )
    return redacted(payload.provider, row)


INTEGRATIONS_REMOVE_TOOL = Tool(
    name="bsnexus_integrations_remove",
    description="Disable an integration and clear its api_key.",
    input_schema=IntegrationsRemoveInput,
    output_schema=IntegrationConfigResponse,
    handler=_integrations_remove_handler,
    required_scopes=[_SCOPE_INTEGRATIONS_WRITE],
    audit_event="nexus.integration.updated",
)


class IntegrationsTestInput(BaseModel):
    provider: IntegrationProvider
    model_config = ConfigDict(extra="forbid")


class IntegrationsTestOutput(BaseModel):
    ok: bool
    status: str
    detail: str | None = None
    checked_at: datetime


async def _integrations_test_handler(payload: IntegrationsTestInput, ctx: ToolContext) -> IntegrationsTestOutput:
    """Lightweight reachability probe — reports row state without making
    an outbound HTTP call. The REST ``/integrations/{provider}/test``
    endpoint does the live probe; MCP intentionally avoids the egress
    side-effect from a tool surface."""
    db = _require_db(ctx)
    tenant_id = _require_tenant(ctx)
    stmt = select(TenantIntegrationConfig).where(
        TenantIntegrationConfig.tenant_id == tenant_id,
        TenantIntegrationConfig.provider == payload.provider,
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if row is None or not row.enabled or not row.base_url:
        return IntegrationsTestOutput(ok=False, status="disabled", detail="enable + set base URL first", checked_at=now)
    return IntegrationsTestOutput(
        ok=True, status="configured", detail="row enabled with base_url + api_key", checked_at=now
    )


INTEGRATIONS_TEST_TOOL = Tool(
    name="bsnexus_integrations_test",
    description="Report integration row state. Live HTTP probes go through the REST endpoint.",
    input_schema=IntegrationsTestInput,
    output_schema=IntegrationsTestOutput,
    handler=_integrations_test_handler,
    required_scopes=[_SCOPE_INTEGRATIONS_READ],
)


# ── catalog + registration ────────────────────────────────────────


_ADMIN_TOOLS: tuple[Tool, ...] = (
    PROJECTS_LIST_TOOL,
    PROJECTS_SHOW_TOOL,
    PROJECTS_CREATE_TOOL,
    PROJECTS_ARCHIVE_TOOL,
    REQUESTS_LIST_TOOL,
    REQUESTS_SHOW_TOOL,
    REQUESTS_CREATE_TOOL,
    REQUESTS_UPDATE_TOOL,
    DECISIONS_LIST_TOOL,
    DECISIONS_SHOW_TOOL,
    DECISIONS_LOCK_TOOL,
    DECISIONS_UNLOCK_TOOL,
    DELIVERABLES_LIST_TOOL,
    DELIVERABLES_SHOW_TOOL,
    DELIVERABLES_ATTACH_TOOL,
    EVENTS_LIST_TOOL,
    INTEGRATIONS_LIST_TOOL,
    INTEGRATIONS_ADD_TOOL,
    INTEGRATIONS_REMOVE_TOOL,
    INTEGRATIONS_TEST_TOOL,
)


ADMIN_TOOL_NAMES: tuple[str, ...] = tuple(t.name for t in _ADMIN_TOOLS)


def register_admin_tools(registry: Any) -> None:
    """Register every admin tool on ``registry``.

    Idempotent only across distinct registries — re-registering on the
    same registry raises (registry surface guarantees uniqueness).
    """
    for tool in _ADMIN_TOOLS:
        registry.register(tool)


__all__ = [
    "ADMIN_TOOL_NAMES",
    "DECISIONS_LIST_TOOL",
    "DECISIONS_LOCK_TOOL",
    "DECISIONS_SHOW_TOOL",
    "DECISIONS_UNLOCK_TOOL",
    "DELIVERABLES_ATTACH_TOOL",
    "DELIVERABLES_LIST_TOOL",
    "DELIVERABLES_SHOW_TOOL",
    "DecisionsListInput",
    "DecisionsListOutput",
    "DecisionsLockInput",
    "DecisionsShowInput",
    "DecisionsUnlockInput",
    "DeliverablesAttachInput",
    "DeliverablesListInput",
    "DeliverablesListOutput",
    "DeliverablesShowInput",
    "EVENTS_LIST_TOOL",
    "EventsListInput",
    "INTEGRATIONS_ADD_TOOL",
    "INTEGRATIONS_LIST_TOOL",
    "INTEGRATIONS_REMOVE_TOOL",
    "INTEGRATIONS_TEST_TOOL",
    "IntegrationsAddInput",
    "IntegrationsListInput",
    "IntegrationsRemoveInput",
    "IntegrationsTestInput",
    "IntegrationsTestOutput",
    "PROJECTS_ARCHIVE_TOOL",
    "PROJECTS_CREATE_TOOL",
    "PROJECTS_LIST_TOOL",
    "PROJECTS_SHOW_TOOL",
    "ProjectsArchiveInput",
    "ProjectsArchiveOutput",
    "ProjectsCreateInput",
    "ProjectsListInput",
    "ProjectsListOutput",
    "ProjectsShowInput",
    "REQUESTS_CREATE_TOOL",
    "REQUESTS_LIST_TOOL",
    "REQUESTS_SHOW_TOOL",
    "REQUESTS_UPDATE_TOOL",
    "RequestsCreateInput",
    "RequestsListInput",
    "RequestsListOutput",
    "RequestsShowInput",
    "RequestsUpdateInput",
    "register_admin_tools",
]
