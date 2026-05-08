"""Admin MCP tools — first-class :class:`Tool` definitions (TASK-004).

Each CLI sub-app maps to one or more ``bsnexus_<subapp>_<action>`` tools
on the shared :class:`ToolRegistry`. These tests verify:

  * the catalog is complete (20 admin tools across 6 sub-apps);
  * every tool declares Pydantic input/output schemas (no Typer
    auto-derivation);
  * scope gates fire before the handler runs;
  * one happy-path :func:`call_tool` per sub-app dispatches end-to-end
    with bootstrap-style admin auth and the right side effects (DB
    rows + audit_outbox row for mutations).
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
import structlog
from bsvibe_audit import AuditOutboxRecord
from bsvibe_authz import User
from pydantic import BaseModel
from sqlalchemy import select

from backend.src.mcp.admin_tools import (
    ADMIN_TOOL_NAMES,
    register_admin_tools,
)
from backend.src.mcp.api import (
    Tool,
    ToolContext,
    ToolHandlerError,
    ToolPermissionError,
    ToolRegistry,
)
from backend.src.models import (
    Decision,
    Deliverable,
    DeliverableType,
    Project,
    Request,
    RequestStatus,
    TenantIntegrationConfig,
)
from backend.src.models.tenant_integration_config import IntegrationProvider


# ── helpers ────────────────────────────────────────────────────────


def _admin_ctx(*, db, tenant_id: uuid.UUID, scope: str = "*") -> ToolContext:
    """Build a :class:`ToolContext` for an admin/bootstrap caller.

    ``scope=*`` mirrors what :func:`bsvibe_authz.verify_bootstrap_token`
    grants — the bootstrap token is the BSV admin master key.
    """
    user = User(
        id="admin@bsvibe.dev",
        is_service=True,
        scope=[scope],
        active_tenant_id=str(tenant_id),
    )
    return ToolContext(
        settings=MagicMock(),
        user=user,
        db=db,
        audit_session=db,
        logger=structlog.get_logger("test.mcp.admin"),
    )


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    register_admin_tools(reg)
    return reg


async def _audit_event_types(db_session) -> list[str]:
    rows = (await db_session.execute(select(AuditOutboxRecord))).scalars().all()
    return [r.event_type for r in rows]


# ── catalog & schema surface ──────────────────────────────────────


def test_register_admin_tools_exposes_full_catalog() -> None:
    reg = _registry()
    names = set(reg.names())
    assert names == set(ADMIN_TOOL_NAMES)
    # 20 admin tools per .agent/mcp-inventory.md: 4 projects + 4 requests
    # + 4 decisions + 3 deliverables + 1 events + 4 integrations.
    assert len(names) == 20


def test_admin_tools_have_explicit_pydantic_schemas() -> None:
    """Every tool MUST declare Pydantic input/output schemas — no Typer
    auto-derivation. The JSON schema returned by ``ListTools`` comes from
    these classes."""
    reg = _registry()
    for name in reg.names():
        tool = reg.get(name)
        assert isinstance(tool, Tool)
        assert issubclass(tool.input_schema, BaseModel)
        assert issubclass(tool.output_schema, BaseModel)
        assert tool.input_schema.model_json_schema()["type"] == "object"
        assert tool.output_schema.model_json_schema()["type"] == "object"


def test_admin_tools_carry_admin_scopes_not_run_scope() -> None:
    """Admin tools must NOT require the domain run scope — admin bearer
    tokens never carry it. They require ``bsnexus:<resource>:<action>``."""
    from backend.src.mcp.domain_tools import DOMAIN_RUN_SCOPE

    reg = _registry()
    for name in reg.names():
        tool = reg.get(name)
        assert DOMAIN_RUN_SCOPE not in tool.required_scopes
        assert any(s.startswith("bsnexus:") for s in tool.required_scopes), name


def test_admin_tools_input_never_accepts_token_arg() -> None:
    """No admin tool input schema accepts a raw bearer token — auth comes
    from :class:`ToolContext`. Guards against a refactor that reintroduces
    a 'token' field on the wire."""
    reg = _registry()
    for name in reg.names():
        props = set(reg.get(name).input_schema.model_json_schema().get("properties", {}).keys())
        assert "token" not in props
        assert "auth_token" not in props
        assert "bearer" not in props


@pytest.mark.asyncio
async def test_admin_tool_rejects_caller_without_required_scope(db_session, mock_tenant_id, seeded_tenant):
    reg = _registry()
    user = User(
        id="weakly-scoped",
        scope=["unrelated:scope"],
        active_tenant_id=str(mock_tenant_id),
    )
    ctx = ToolContext(
        settings=MagicMock(),
        user=user,
        db=db_session,
        audit_session=db_session,
        logger=structlog.get_logger("test"),
    )
    with pytest.raises(ToolPermissionError):
        await reg.call_tool("bsnexus_projects_list", {}, ctx)


# ── projects: list (sub-app #1, read) ─────────────────────────────


@pytest.mark.asyncio
async def test_projects_list_returns_tenant_rows(db_session, mock_tenant_id, seeded_tenant):
    db_session.add(Project(tenant_id=mock_tenant_id, name="alpha"))
    db_session.add(Project(tenant_id=mock_tenant_id, name="beta"))
    await db_session.commit()

    reg = _registry()
    out = await reg.call_tool(
        "bsnexus_projects_list",
        {},
        _admin_ctx(db=db_session, tenant_id=mock_tenant_id),
    )
    assert {row.name for row in out.items} == {"alpha", "beta"}


# ── projects: create (sub-app #1, mutation + audit) ───────────────


@pytest.mark.asyncio
async def test_projects_create_persists_row_and_emits_audit(db_session, mock_tenant_id, seeded_tenant):
    """Goes through the same ``_persist_project_with_audit`` helper the
    REST router uses, so the ``nexus.project.created`` outbox row is
    identical to what the REST POST would produce."""
    reg = _registry()
    out = await reg.call_tool(
        "bsnexus_projects_create",
        {"name": "delta", "description": "demo"},
        _admin_ctx(db=db_session, tenant_id=mock_tenant_id),
    )
    assert out.name == "delta"
    assert out.tenant_id == mock_tenant_id

    # Domain row landed.
    rows = (await db_session.execute(select(Project).where(Project.name == "delta"))).scalars().all()
    assert len(rows) == 1

    # Audit outbox row landed via the helper's ``@audit_emit`` decorator.
    types = await _audit_event_types(db_session)
    assert "nexus.project.created" in types


# ── requests: list (sub-app #2, read) ─────────────────────────────


@pytest.mark.asyncio
async def test_requests_list_returns_request_rows(db_session, mock_tenant_id, seeded_tenant):
    project = Project(tenant_id=mock_tenant_id, name="p1")
    db_session.add(project)
    await db_session.flush()
    db_session.add(
        Request(
            tenant_id=mock_tenant_id,
            project_id=project.id,
            intent_summary="ship X",
            status=RequestStatus.open,
        )
    )
    await db_session.commit()

    reg = _registry()
    out = await reg.call_tool(
        "bsnexus_requests_list",
        {"project_id": str(project.id)},
        _admin_ctx(db=db_session, tenant_id=mock_tenant_id),
    )
    assert len(out.items) == 1
    assert out.items[0].intent_summary == "ship X"


# ── decisions: list + lock (sub-app #3) ───────────────────────────


@pytest.mark.asyncio
async def test_decisions_list_returns_inbox(db_session, mock_tenant_id, seeded_tenant):
    project = Project(tenant_id=mock_tenant_id, name="p1")
    db_session.add(project)
    await db_session.flush()
    db_session.add(
        Decision(
            tenant_id=mock_tenant_id,
            project_id=project.id,
            question="postgres or sqlite?",
            options=["postgres", "sqlite"],
            blocking=True,
        )
    )
    await db_session.commit()

    reg = _registry()
    out = await reg.call_tool(
        "bsnexus_decisions_list",
        {"project_id": str(project.id)},
        _admin_ctx(db=db_session, tenant_id=mock_tenant_id),
    )
    assert len(out.items) == 1
    assert out.items[0].question == "postgres or sqlite?"


@pytest.mark.asyncio
async def test_decisions_lock_resolves_and_emits_audit(db_session, mock_tenant_id, seeded_tenant):
    """Delegates to ``_apply_decision_resolution_with_audit`` so the
    ``nexus.decision.resolved`` outbox row matches the REST contract.

    The inner SSE / knowledge-record side effects are not mirrored —
    those are presentation-layer concerns the MCP path leaves alone.
    """
    project = Project(tenant_id=mock_tenant_id, name="p1")
    db_session.add(project)
    await db_session.flush()
    decision = Decision(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        question="?",
        options=["yes", "no"],
        blocking=True,
    )
    db_session.add(decision)
    await db_session.commit()
    await db_session.refresh(decision)

    reg = _registry()
    out = await reg.call_tool(
        "bsnexus_decisions_lock",
        {"decision_id": str(decision.id), "resolution": "yes", "resolved_by": "founder"},
        _admin_ctx(db=db_session, tenant_id=mock_tenant_id),
    )
    assert out.id == decision.id
    assert out.resolution == "yes"

    types = await _audit_event_types(db_session)
    assert "nexus.decision.resolved" in types


@pytest.mark.asyncio
async def test_decisions_unlock_is_not_supported(db_session, mock_tenant_id, seeded_tenant):
    """Mirrors the CLI: backend exposes no reopen endpoint, so the tool
    returns a typed handler error rather than silently failing."""
    project = Project(tenant_id=mock_tenant_id, name="p1")
    db_session.add(project)
    await db_session.flush()
    decision = Decision(tenant_id=mock_tenant_id, project_id=project.id, question="?", options=[])
    db_session.add(decision)
    await db_session.commit()

    reg = _registry()
    with pytest.raises(ToolHandlerError):
        await reg.call_tool(
            "bsnexus_decisions_unlock",
            {"decision_id": str(decision.id)},
            _admin_ctx(db=db_session, tenant_id=mock_tenant_id),
        )


# ── deliverables: list (sub-app #4, read) ─────────────────────────


@pytest.mark.asyncio
async def test_deliverables_list_returns_rows(db_session, mock_tenant_id, seeded_tenant):
    project = Project(tenant_id=mock_tenant_id, name="p1")
    db_session.add(project)
    await db_session.flush()
    db_session.add(
        Deliverable(
            tenant_id=mock_tenant_id,
            project_id=project.id,
            type=DeliverableType.doc,
            title="readme.md",
        )
    )
    await db_session.commit()

    reg = _registry()
    out = await reg.call_tool(
        "bsnexus_deliverables_list",
        {"project_id": str(project.id)},
        _admin_ctx(db=db_session, tenant_id=mock_tenant_id),
    )
    assert len(out.items) == 1
    assert out.items[0].title == "readme.md"


# ── events: list (sub-app #5) ─────────────────────────────────────


@pytest.mark.asyncio
async def test_events_list_raises_not_supported(db_session, mock_tenant_id, seeded_tenant):
    """SSE is a streaming concern — MCP exposes the tool for catalog
    completeness but the handler raises pointing callers at the HTTP
    SSE endpoint. Mirrors the CLI's "not supported" pattern for
    ``deliverables attach`` / ``decisions unlock``."""
    project = Project(tenant_id=mock_tenant_id, name="p1")
    db_session.add(project)
    await db_session.commit()
    reg = _registry()
    with pytest.raises(ToolHandlerError):
        await reg.call_tool(
            "bsnexus_events_list",
            {"project_id": str(project.id)},
            _admin_ctx(db=db_session, tenant_id=mock_tenant_id),
        )


# ── integrations: list + add (sub-app #6) ─────────────────────────


@pytest.mark.asyncio
async def test_integrations_list_returns_redacted_view(db_session, mock_tenant_id, seeded_tenant):
    """No api_key on the wire — only ``has_api_key``. Mirrors the REST
    safety contract."""
    db_session.add(
        TenantIntegrationConfig(
            tenant_id=mock_tenant_id,
            provider=IntegrationProvider.bsage,
            enabled=True,
            base_url="https://bsage.test",
            api_key_encrypted="enc:secret",
        )
    )
    await db_session.commit()

    reg = _registry()
    out = await reg.call_tool(
        "bsnexus_integrations_list",
        {},
        _admin_ctx(db=db_session, tenant_id=mock_tenant_id),
    )
    assert out.bsage.enabled is True
    assert out.bsage.has_api_key is True
    dumped = out.model_dump()
    assert "api_key" not in dumped["bsage"]


@pytest.mark.asyncio
async def test_integrations_add_upserts_encrypts_key_and_audits(db_session, mock_tenant_id, seeded_tenant, monkeypatch):
    """The api_key MUST be encrypted at rest — the raw value never lands
    on the row. Audit fires via the dispatcher (handler doesn't emit
    inline so the tool sets ``audit_event`` for the dispatcher)."""
    captured: list = []

    async def fake_emit(event, *, session):
        captured.append(event)

    monkeypatch.setattr("backend.src.mcp.api.safe_emit", fake_emit)

    reg = _registry()
    out = await reg.call_tool(
        "bsnexus_integrations_add",
        {
            "provider": "bsage",
            "base_url": "https://bsage.test",
            "api_key": "sekrit",
            "enabled": True,
        },
        _admin_ctx(db=db_session, tenant_id=mock_tenant_id),
    )
    assert out.provider == IntegrationProvider.bsage
    assert out.enabled is True
    assert out.has_api_key is True

    # Encryption: the row's api_key_encrypted must NOT contain "sekrit"
    # verbatim (Fernet output is base64-armoured).
    rows = (
        (
            await db_session.execute(
                select(TenantIntegrationConfig).where(
                    TenantIntegrationConfig.tenant_id == mock_tenant_id,
                    TenantIntegrationConfig.provider == IntegrationProvider.bsage,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].api_key_encrypted is not None
    assert "sekrit" not in rows[0].api_key_encrypted

    # Audit fired via dispatcher path.
    assert any(getattr(e, "event_type", "") == "nexus.integration.updated" for e in captured)


# ── deliverables_attach is not supported ──────────────────────────


@pytest.mark.asyncio
async def test_deliverables_attach_raises_not_supported(db_session, mock_tenant_id, seeded_tenant):
    project = Project(tenant_id=mock_tenant_id, name="p1")
    db_session.add(project)
    await db_session.flush()
    deliv = Deliverable(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        type=DeliverableType.doc,
        title="x",
    )
    db_session.add(deliv)
    await db_session.commit()

    reg = _registry()
    with pytest.raises(ToolHandlerError):
        await reg.call_tool(
            "bsnexus_deliverables_attach",
            {"deliverable_id": str(deliv.id), "path": "/tmp/x"},
            _admin_ctx(db=db_session, tenant_id=mock_tenant_id),
        )


# ── coverage: read/show/archive happy paths + write side effects ──
#
# These tests don't add new contract — they exercise the handlers that
# the hand-picked behaviour tests above didn't cover, so the new module
# clears the project-wide ``--cov-fail-under=80`` gate.


@pytest.mark.asyncio
async def test_projects_show_returns_single_row(db_session, mock_tenant_id, seeded_tenant):
    project = Project(tenant_id=mock_tenant_id, name="readme")
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)
    reg = _registry()
    out = await reg.call_tool(
        "bsnexus_projects_show",
        {"project_id": str(project.id)},
        _admin_ctx(db=db_session, tenant_id=mock_tenant_id),
    )
    assert out.id == project.id


@pytest.mark.asyncio
async def test_projects_archive_deletes_row(db_session, mock_tenant_id, seeded_tenant):
    project = Project(tenant_id=mock_tenant_id, name="trash")
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)
    reg = _registry()
    out = await reg.call_tool(
        "bsnexus_projects_archive",
        {"project_id": str(project.id)},
        _admin_ctx(db=db_session, tenant_id=mock_tenant_id),
    )
    assert out.archived == str(project.id)
    rows = (await db_session.execute(select(Project).where(Project.id == project.id))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_requests_show_and_create_and_update(db_session, mock_tenant_id, seeded_tenant):
    project = Project(tenant_id=mock_tenant_id, name="p1")
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)
    reg = _registry()
    ctx = _admin_ctx(db=db_session, tenant_id=mock_tenant_id)

    created = await reg.call_tool(
        "bsnexus_requests_create",
        {"project_id": str(project.id), "intent_summary": "draft proposal"},
        ctx,
    )
    assert created.intent_summary == "draft proposal"

    shown = await reg.call_tool(
        "bsnexus_requests_show",
        {"request_id": str(created.id)},
        ctx,
    )
    assert shown.id == created.id

    updated = await reg.call_tool(
        "bsnexus_requests_update",
        {"request_id": str(created.id), "status": "completed", "intent_summary": "shipped"},
        ctx,
    )
    assert updated.status == RequestStatus.completed
    assert updated.intent_summary == "shipped"


@pytest.mark.asyncio
async def test_decisions_show_and_deliverables_show(db_session, mock_tenant_id, seeded_tenant):
    project = Project(tenant_id=mock_tenant_id, name="p1")
    db_session.add(project)
    await db_session.flush()
    decision = Decision(tenant_id=mock_tenant_id, project_id=project.id, question="?", options=["y"])
    deliv = Deliverable(tenant_id=mock_tenant_id, project_id=project.id, type=DeliverableType.doc, title="d")
    db_session.add_all([decision, deliv])
    await db_session.commit()
    await db_session.refresh(decision)
    await db_session.refresh(deliv)

    reg = _registry()
    ctx = _admin_ctx(db=db_session, tenant_id=mock_tenant_id)
    decision_out = await reg.call_tool("bsnexus_decisions_show", {"decision_id": str(decision.id)}, ctx)
    assert decision_out.id == decision.id

    deliv_out = await reg.call_tool("bsnexus_deliverables_show", {"deliverable_id": str(deliv.id)}, ctx)
    assert deliv_out.id == deliv.id


@pytest.mark.asyncio
async def test_integrations_remove_and_test(db_session, mock_tenant_id, seeded_tenant):
    db_session.add(
        TenantIntegrationConfig(
            tenant_id=mock_tenant_id,
            provider=IntegrationProvider.bsage,
            enabled=True,
            base_url="https://bsage.test",
            api_key_encrypted="enc:secret",
        )
    )
    await db_session.commit()

    reg = _registry()
    ctx = _admin_ctx(db=db_session, tenant_id=mock_tenant_id)

    test_out = await reg.call_tool("bsnexus_integrations_test", {"provider": "bsage"}, ctx)
    assert test_out.ok is True
    assert test_out.status == "configured"

    removed = await reg.call_tool("bsnexus_integrations_remove", {"provider": "bsage"}, ctx)
    assert removed.enabled is False
    assert removed.has_api_key is False

    test_after = await reg.call_tool("bsnexus_integrations_test", {"provider": "bsage"}, ctx)
    assert test_after.ok is False
    assert test_after.status == "disabled"


@pytest.mark.asyncio
async def test_handler_rejects_missing_tenant_in_context(db_session, mock_tenant_id, seeded_tenant):
    """Tenant defence-in-depth: a caller whose User has no
    ``active_tenant_id`` gets a typed handler error rather than a 500."""
    user = User(id="anon", scope=["*"], active_tenant_id=None)
    ctx = ToolContext(
        settings=MagicMock(),
        user=user,
        db=db_session,
        audit_session=db_session,
        logger=structlog.get_logger("test"),
    )
    reg = _registry()
    with pytest.raises(ToolHandlerError):
        await reg.call_tool("bsnexus_projects_list", {}, ctx)
