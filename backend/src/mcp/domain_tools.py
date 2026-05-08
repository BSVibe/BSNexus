"""First-class :class:`Tool` definitions for the six domain MCP tools.

These are the run-scoped tools the BSGateway worker's CLI (claude /
codex / opencode) calls during an execution run — ``decision.create``,
``decision.wait``, ``artifact.list``, ``artifact.read``,
``deliverable_report``, ``knowledge.search``.

TASK-003 migrates them from FastMCP ``@app.tool()`` decorators that
delegated to :mod:`backend.src.mcp.tools` directly into first-class
:class:`Tool` instances on the shared :class:`ToolRegistry`. The
underlying ``tools.py`` async functions stay untouched (the existing
``test_mcp_tools`` contract is preserved); the new layer wraps them in
typed Pydantic input/output schemas, threads run-scoped context through
:class:`ToolContext`, and emits the matching ``nexus.*`` audit event on
mutating success.

Auth model: domain tools authenticate via the run-scoped HMAC token
the dispatcher mints per ``ExecutionRun`` (see :mod:`backend.src.mcp.auth`).
The FastMCP transport gate verifies the token, builds a synthetic
service :class:`User` carrying the ``DOMAIN_RUN_SCOPE`` scope plus
``active_tenant_id`` from the claim, and stuffs ``run_id`` /
``project_id`` into the :class:`ToolContext`. The bsvibe-authz scope
gate then grants every domain tool's required scope. Admin callers
without ``DOMAIN_RUN_SCOPE`` get a :class:`ToolPermissionError` —
domain tools should not be callable with admin bearer tokens (different
threat model: a bootstrap-token holder can't impersonate a specific
run).
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from pydantic import BaseModel, Field

from backend.src.mcp.api import Tool, ToolContext
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

logger = structlog.get_logger(__name__)


# Single sentinel scope every domain tool requires. The FastMCP gate
# (``server.attach_to_app``) injects this scope into the synthetic
# service user it builds from the verified run-scoped claim. An admin
# bearer token does not carry it (admins use distinct ``bsnexus:*``
# scopes, see ``admin_tools.py`` in TASK-004).
DOMAIN_RUN_SCOPE = "bsnexus:mcp:run"


# ── shared output fragments ────────────────────────────────────────


class ArtifactItem(BaseModel):
    """One row in :class:`ArtifactListOutput.items`."""

    id: str
    title: str
    type: str
    status: str


class KnowledgeHit(BaseModel):
    """One fragment surfaced by :class:`KnowledgeSearchOutput.hits`."""

    title: str
    excerpt: str


# ── decision_create ────────────────────────────────────────────────


class DecisionCreateInput(BaseModel):
    question: str = Field(..., min_length=1)
    options: list[str] = Field(default_factory=list)
    context: str | None = None


class DecisionCreateOutput(BaseModel):
    decision_id: str


async def _decision_create_handler(payload: DecisionCreateInput, ctx: ToolContext) -> DecisionCreateOutput:
    """Persist a Decision row tied to the active run.

    Run / tenant / project all come from :class:`ToolContext` populated
    by the FastMCP gate from the verified token claim — never trusted
    from the input payload (that would let a malicious agent open
    decisions on a run other than its own).
    """
    if ctx.db is None or ctx.run_id is None or ctx.project_id is None:
        raise MCPToolError("missing run-scoped context")
    tenant_id = ctx.user.active_tenant_id
    if not tenant_id:
        raise MCPToolError("missing tenant in context")
    decision_id = await create_decision(
        question=payload.question,
        options=list(payload.options),
        context=payload.context,
        run_id=uuid.UUID(ctx.run_id),
        tenant_id=uuid.UUID(tenant_id),
        project_id=uuid.UUID(ctx.project_id),
        db=ctx.db,
    )
    return DecisionCreateOutput(decision_id=str(decision_id))


DECISION_CREATE_TOOL = Tool(
    name="decision_create",
    description="Open a Decision row for the founder to resolve. Returns {decision_id}.",
    input_schema=DecisionCreateInput,
    output_schema=DecisionCreateOutput,
    handler=_decision_create_handler,
    required_scopes=[DOMAIN_RUN_SCOPE],
    audit_event="nexus.decision.created",
)


# ── decision_wait ──────────────────────────────────────────────────


class DecisionWaitInput(BaseModel):
    decision_id: str = Field(..., min_length=1)
    timeout_seconds: float = 3000.0


class DecisionWaitOutput(BaseModel):
    """Union shape: ``{choice, notes}`` on resolve, ``{error}`` on
    timeout / cross-tenant rejection.

    Mirrors the existing FastMCP wrapper which surfaced the error
    in-band so the CLI's tool-call loop could decide how to recover
    (typically: emit an "awaiting decision" deliverable and exit).
    """

    choice: str | None = None
    notes: str | None = None
    error: str | None = None


async def _decision_wait_handler(payload: DecisionWaitInput, ctx: ToolContext) -> DecisionWaitOutput:
    if ctx.db is None:
        raise MCPToolError("missing db session in context")
    tenant_id = ctx.user.active_tenant_id
    if not tenant_id:
        raise MCPToolError("missing tenant in context")
    try:
        result = await wait_for_decision(
            decision_id=uuid.UUID(payload.decision_id),
            tenant_id=uuid.UUID(tenant_id),
            queue=get_decision_queue(),
            timeout_seconds=payload.timeout_seconds,
            db=ctx.db,
        )
    except MCPToolError as exc:
        return DecisionWaitOutput(error=str(exc))
    return DecisionWaitOutput(
        choice=result.get("choice"),
        notes=result.get("notes", ""),
    )


DECISION_WAIT_TOOL = Tool(
    name="decision_wait",
    description="Block until the founder resolves the decision. Returns {choice, notes} or {error}.",
    input_schema=DecisionWaitInput,
    output_schema=DecisionWaitOutput,
    handler=_decision_wait_handler,
    required_scopes=[DOMAIN_RUN_SCOPE],
)


# ── artifact_list ──────────────────────────────────────────────────


class ArtifactListInput(BaseModel):
    request_id: str = Field(..., min_length=1)


class ArtifactListOutput(BaseModel):
    items: list[ArtifactItem] = Field(default_factory=list)


async def _artifact_list_handler(payload: ArtifactListInput, ctx: ToolContext) -> ArtifactListOutput:
    if ctx.db is None:
        raise MCPToolError("missing db session in context")
    tenant_id = ctx.user.active_tenant_id
    if not tenant_id:
        raise MCPToolError("missing tenant in context")
    rows = await list_run_artifacts(
        request_id=uuid.UUID(payload.request_id),
        tenant_id=uuid.UUID(tenant_id),
        db=ctx.db,
    )
    return ArtifactListOutput(items=[ArtifactItem(**row) for row in rows])


ARTIFACT_LIST_TOOL = Tool(
    name="artifact_list",
    description="List deliverables for a request. Returns [{id, title, type, status}].",
    input_schema=ArtifactListInput,
    output_schema=ArtifactListOutput,
    handler=_artifact_list_handler,
    required_scopes=[DOMAIN_RUN_SCOPE],
)


# ── artifact_read ──────────────────────────────────────────────────


class ArtifactReadInput(BaseModel):
    deliverable_id: str = Field(..., min_length=1)


class ArtifactReadOutput(BaseModel):
    """``{body}`` on success, ``{body: '', error}`` on missing /
    cross-tenant. The error path matches the FastMCP wrapper's previous
    return shape so existing CLI consumers don't change."""

    body: str = ""
    error: str | None = None


async def _artifact_read_handler(payload: ArtifactReadInput, ctx: ToolContext) -> ArtifactReadOutput:
    if ctx.db is None:
        raise MCPToolError("missing db session in context")
    tenant_id = ctx.user.active_tenant_id
    if not tenant_id:
        raise MCPToolError("missing tenant in context")
    try:
        body = await read_artifact(
            deliverable_id=uuid.UUID(payload.deliverable_id),
            tenant_id=uuid.UUID(tenant_id),
            db=ctx.db,
        )
    except MCPToolError as exc:
        return ArtifactReadOutput(body="", error=str(exc))
    return ArtifactReadOutput(body=body)


ARTIFACT_READ_TOOL = Tool(
    name="artifact_read",
    description="Return the inline body of a Deliverable's current version. Returns {body} or {body: '', error}.",
    input_schema=ArtifactReadInput,
    output_schema=ArtifactReadOutput,
    handler=_artifact_read_handler,
    required_scopes=[DOMAIN_RUN_SCOPE],
)


# ── deliverable_report ─────────────────────────────────────────────


class DeliverableReportInput(BaseModel):
    title: str = Field(..., min_length=1)
    body: str
    links: list[str] | None = None


class DeliverableReportOutput(BaseModel):
    deliverable_id: str


async def _deliverable_report_handler(payload: DeliverableReportInput, ctx: ToolContext) -> DeliverableReportOutput:
    if ctx.db is None or ctx.run_id is None or ctx.project_id is None:
        raise MCPToolError("missing run-scoped context")
    tenant_id = ctx.user.active_tenant_id
    if not tenant_id:
        raise MCPToolError("missing tenant in context")
    deliv_id = await report_deliverable(
        title=payload.title,
        body=payload.body,
        links=list(payload.links) if payload.links else None,
        run_id=uuid.UUID(ctx.run_id),
        tenant_id=uuid.UUID(tenant_id),
        project_id=uuid.UUID(ctx.project_id),
        db=ctx.db,
    )
    return DeliverableReportOutput(deliverable_id=str(deliv_id))


DELIVERABLE_REPORT_TOOL = Tool(
    name="deliverable_report",
    description="Persist a Deliverable + DeliverableVersion claude wrote. Returns {deliverable_id}.",
    input_schema=DeliverableReportInput,
    output_schema=DeliverableReportOutput,
    handler=_deliverable_report_handler,
    required_scopes=[DOMAIN_RUN_SCOPE],
    audit_event="nexus.deliverable.created",
)


# ── knowledge_search ───────────────────────────────────────────────


class KnowledgeSearchInput(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = 10


class KnowledgeSearchOutput(BaseModel):
    hits: list[KnowledgeHit] = Field(default_factory=list)


async def _knowledge_search_handler(payload: KnowledgeSearchInput, ctx: ToolContext) -> KnowledgeSearchOutput:
    """Resolve the tenant's BSage client fresh per call so a
    just-flipped integration takes effect immediately. Disabled BSage
    returns ``{hits: []}`` (Noop fallback) — the tool never raises so
    claude can keep going in degraded mode."""
    if ctx.db is None:
        raise MCPToolError("missing db session in context")
    tenant_id = ctx.user.active_tenant_id
    if not tenant_id:
        raise MCPToolError("missing tenant in context")

    # Local imports — :mod:`composer` and :mod:`integrations` pull in
    # SQLAlchemy / pydantic-settings; importing them at module top
    # leaks BSage configuration assumptions into anyone touching the
    # MCP module (e.g. the CLI ``mcp list-tools`` command).
    from backend.src.core.composer import resolve_knowledge_client  # noqa: PLC0415
    from backend.src.core.composer.knowledge_client import NoopKnowledgeClient  # noqa: PLC0415
    from backend.src.core.integrations import get_tenant_integration_snapshot  # noqa: PLC0415

    integrations = await get_tenant_integration_snapshot(ctx.db, uuid.UUID(tenant_id))
    knowledge = resolve_knowledge_client(integrations.bsage)
    if isinstance(knowledge, NoopKnowledgeClient):
        return KnowledgeSearchOutput(hits=[])

    rows: list[dict[str, Any]] = await search_knowledge(
        query=payload.query,
        knowledge_client=knowledge,
        top_k=payload.top_k,
    )
    return KnowledgeSearchOutput(hits=[KnowledgeHit(**row) for row in rows])


KNOWLEDGE_SEARCH_TOOL = Tool(
    name="knowledge_search",
    description="Search BSage. Returns {hits: [{title, excerpt}]} (empty if BSage disabled).",
    input_schema=KnowledgeSearchInput,
    output_schema=KnowledgeSearchOutput,
    handler=_knowledge_search_handler,
    required_scopes=[DOMAIN_RUN_SCOPE],
)


# ── registration helper ────────────────────────────────────────────


_DOMAIN_TOOLS: tuple[Tool, ...] = (
    DECISION_CREATE_TOOL,
    DECISION_WAIT_TOOL,
    ARTIFACT_LIST_TOOL,
    ARTIFACT_READ_TOOL,
    DELIVERABLE_REPORT_TOOL,
    KNOWLEDGE_SEARCH_TOOL,
)


def register_domain_tools(registry: Any) -> None:
    """Register every domain tool on ``registry``.

    Idempotent only across distinct registries — re-registering on the
    same registry raises (registry surface guarantees uniqueness).
    """
    for tool in _DOMAIN_TOOLS:
        registry.register(tool)


__all__ = [
    "ARTIFACT_LIST_TOOL",
    "ARTIFACT_READ_TOOL",
    "ArtifactItem",
    "ArtifactListInput",
    "ArtifactListOutput",
    "ArtifactReadInput",
    "ArtifactReadOutput",
    "DECISION_CREATE_TOOL",
    "DECISION_WAIT_TOOL",
    "DELIVERABLE_REPORT_TOOL",
    "DOMAIN_RUN_SCOPE",
    "DecisionCreateInput",
    "DecisionCreateOutput",
    "DecisionWaitInput",
    "DecisionWaitOutput",
    "DeliverableReportInput",
    "DeliverableReportOutput",
    "KNOWLEDGE_SEARCH_TOOL",
    "KnowledgeHit",
    "KnowledgeSearchInput",
    "KnowledgeSearchOutput",
    "register_domain_tools",
]
