"""Domain MCP tools migrated to first-class :class:`Tool` model (TASK-003).

These tests exercise the ``ToolRegistry`` dispatch path for the six
domain tools — ``decision_create`` / ``decision_wait`` /
``artifact_list`` / ``artifact_read`` / ``deliverable_report`` /
``knowledge_search``. The point is to verify the migration: each tool
is registered with explicit Pydantic input/output schemas, the handler
delegates to the same ``tools.py`` async function the existing tests
pin, audit fires on mutating calls, and run-scoped context (run_id /
project_id / tenant_id) is threaded through ``ToolContext`` rather than
the global contextvar.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
import structlog
from bsvibe_authz import User

from backend.src.mcp.api import (
    Tool,
    ToolContext,
    ToolRegistry,
)
from backend.src.mcp.domain_tools import (
    DOMAIN_RUN_SCOPE,
    register_domain_tools,
)
from backend.src.models import (
    Decision,
    Deliverable,
    DeliverableType,
    DeliverableVersion,
    ExecutionRun,
    Project,
    Request,
    RequestStatus,
    RunPriority,
    RunStatus,
    StorageBackend,
)


# ── helpers ────────────────────────────────────────────────────────


def _ctx_for(
    *,
    db,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
) -> ToolContext:
    """Build a :class:`ToolContext` with a synthetic run-scoped User."""
    user = User(
        id=f"run:{run_id}" if run_id else "run:unknown",
        is_service=True,
        scope=[DOMAIN_RUN_SCOPE],
        active_tenant_id=str(tenant_id),
    )
    return ToolContext(
        settings=MagicMock(),
        user=user,
        db=db,
        audit_session=db,
        logger=structlog.get_logger("test.mcp.domain"),
        run_id=str(run_id) if run_id else None,
        project_id=str(project_id) if project_id else None,
    )


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    register_domain_tools(reg)
    return reg


# ── catalog & schema surface ──────────────────────────────────────


def test_register_domain_tools_exposes_six_tools() -> None:
    reg = _registry()
    names = set(reg.names())
    assert names == {
        "decision_create",
        "decision_wait",
        "artifact_list",
        "artifact_read",
        "deliverable_report",
        "knowledge_search",
    }


def test_domain_tools_have_input_and_output_schemas() -> None:
    reg = _registry()
    for tool_name in reg.names():
        tool = reg.get(tool_name)
        assert isinstance(tool, Tool)
        # Every domain tool has explicit Pydantic schemas — never auto-derived.
        schema = tool.input_schema.model_json_schema()
        assert schema["type"] == "object"
        out_schema = tool.output_schema.model_json_schema()
        assert out_schema["type"] == "object"


def test_mutating_domain_tools_carry_audit_event() -> None:
    reg = _registry()
    assert reg.get("decision_create").audit_event == "nexus.decision.created"
    assert reg.get("deliverable_report").audit_event == "nexus.deliverable.created"
    # Non-mutating tools have no audit event.
    for name in ("artifact_list", "artifact_read", "decision_wait", "knowledge_search"):
        assert reg.get(name).audit_event is None


def test_domain_tools_require_run_scope() -> None:
    reg = _registry()
    for name in reg.names():
        assert DOMAIN_RUN_SCOPE in reg.get(name).required_scopes


# ── decision_create dispatch ──────────────────────────────────────


@pytest.mark.asyncio
async def test_decision_create_dispatches_through_registry(db_session, mock_tenant_id, seeded_tenant, monkeypatch):
    project = Project(tenant_id=mock_tenant_id, name="t")
    db_session.add(project)
    await db_session.flush()
    request = Request(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        intent_summary="x",
        status=RequestStatus.open,
    )
    db_session.add(request)
    await db_session.flush()
    run = ExecutionRun(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        request_id=request.id,
        status=RunStatus.running,
        priority=RunPriority.medium,
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)

    captured: list[Any] = []

    async def fake_emit(event, *, session):
        captured.append(event)

    monkeypatch.setattr("backend.src.mcp.api.safe_emit", fake_emit)

    reg = _registry()
    ctx = _ctx_for(
        db=db_session,
        tenant_id=mock_tenant_id,
        run_id=run.id,
        project_id=project.id,
    )

    out = await reg.call_tool(
        "decision_create",
        {
            "question": "postgres or sqlite?",
            "options": ["postgres", "sqlite"],
            "context": "ambiguity",
        },
        ctx,
    )
    await db_session.commit()
    assert out.decision_id  # uuid string

    # Audit fired on success with the expected event.
    assert len(captured) == 1
    assert captured[0].event_type == "nexus.decision.created"
    assert captured[0].tenant_id == str(mock_tenant_id)


@pytest.mark.asyncio
async def test_decision_create_cross_tenant_wraps_handler_error(db_session, mock_tenant_id, seeded_tenant):
    """The underlying ``MCPToolError`` collapses to ``ToolHandlerError`` —
    the dispatcher never echoes raw exception text (token-leak guard)."""
    from backend.src.mcp.api import ToolHandlerError

    project = Project(tenant_id=mock_tenant_id, name="t")
    db_session.add(project)
    await db_session.flush()
    request = Request(tenant_id=mock_tenant_id, project_id=project.id, intent_summary="x", status=RequestStatus.open)
    db_session.add(request)
    await db_session.flush()
    run = ExecutionRun(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        request_id=request.id,
        status=RunStatus.running,
        priority=RunPriority.medium,
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)

    reg = _registry()
    ctx = _ctx_for(
        db=db_session,
        tenant_id=uuid.uuid4(),  # cross-tenant
        run_id=run.id,
        project_id=project.id,
    )

    with pytest.raises(ToolHandlerError):
        await reg.call_tool(
            "decision_create",
            {"question": "?", "options": [], "context": None},
            ctx,
        )


# ── decision_wait dispatch (already-resolved branch) ──────────────


@pytest.mark.asyncio
async def test_decision_wait_returns_resolved_payload(db_session, mock_tenant_id, seeded_tenant):
    project = Project(tenant_id=mock_tenant_id, name="t")
    db_session.add(project)
    await db_session.flush()
    decision = Decision(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        question="?",
        options=["yes", "no"],
        blocking=True,
        resolution="yes",
    )
    db_session.add(decision)
    await db_session.commit()
    await db_session.refresh(decision)

    reg = _registry()
    ctx = _ctx_for(db=db_session, tenant_id=mock_tenant_id)

    out = await reg.call_tool(
        "decision_wait",
        {"decision_id": str(decision.id), "timeout_seconds": 0.1},
        ctx,
    )
    assert out.choice == "yes"
    assert out.notes == ""
    assert out.error is None


@pytest.mark.asyncio
async def test_decision_wait_cross_tenant_returns_error_field(db_session, mock_tenant_id, seeded_tenant):
    """The handler catches ``MCPToolError`` and surfaces it as the
    ``error`` field of the union output schema — preserves the existing
    FastMCP wrapper behaviour where the CLI sees the error in-band."""
    project = Project(tenant_id=mock_tenant_id, name="t")
    db_session.add(project)
    await db_session.flush()
    decision = Decision(tenant_id=mock_tenant_id, project_id=project.id, question="?", options=[], blocking=True)
    db_session.add(decision)
    await db_session.commit()
    await db_session.refresh(decision)

    reg = _registry()
    ctx = _ctx_for(db=db_session, tenant_id=uuid.uuid4())

    out = await reg.call_tool(
        "decision_wait",
        {"decision_id": str(decision.id), "timeout_seconds": 0.05},
        ctx,
    )
    assert out.error is not None
    assert "tenant" in out.error


# ── artifact_list dispatch ────────────────────────────────────────


@pytest.mark.asyncio
async def test_artifact_list_returns_run_deliverables(db_session, mock_tenant_id, seeded_tenant):
    project = Project(tenant_id=mock_tenant_id, name="t")
    db_session.add(project)
    await db_session.flush()
    request = Request(tenant_id=mock_tenant_id, project_id=project.id, intent_summary="x", status=RequestStatus.open)
    db_session.add(request)
    await db_session.flush()
    deliv = Deliverable(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        request_id=request.id,
        type=DeliverableType.doc,
        title="output.md",
    )
    db_session.add(deliv)
    await db_session.commit()
    await db_session.refresh(deliv)

    reg = _registry()
    ctx = _ctx_for(db=db_session, tenant_id=mock_tenant_id)

    out = await reg.call_tool(
        "artifact_list",
        {"request_id": str(request.id)},
        ctx,
    )
    assert len(out.items) == 1
    assert out.items[0].title == "output.md"
    assert out.items[0].type == "doc"


# ── artifact_read dispatch ────────────────────────────────────────


@pytest.mark.asyncio
async def test_artifact_read_returns_inline_body(db_session, mock_tenant_id, seeded_tenant):
    project = Project(tenant_id=mock_tenant_id, name="t")
    db_session.add(project)
    await db_session.flush()
    deliv = Deliverable(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        type=DeliverableType.doc,
        title="readme",
    )
    db_session.add(deliv)
    await db_session.flush()
    version = DeliverableVersion(
        deliverable_id=deliv.id,
        version_int=1,
        storage_backend=StorageBackend.object,
        content_ref={"inline": "hello inline body"},
        content_hash="x" * 64,
        size_bytes=18,
        created_by_run_id=None,
    )
    db_session.add(version)
    await db_session.flush()
    deliv.current_version_id = version.id
    await db_session.commit()

    reg = _registry()
    ctx = _ctx_for(db=db_session, tenant_id=mock_tenant_id)

    out = await reg.call_tool(
        "artifact_read",
        {"deliverable_id": str(deliv.id)},
        ctx,
    )
    assert out.body == "hello inline body"
    assert out.error is None


@pytest.mark.asyncio
async def test_artifact_read_missing_returns_error_field(db_session, mock_tenant_id, seeded_tenant):
    reg = _registry()
    ctx = _ctx_for(db=db_session, tenant_id=mock_tenant_id)
    out = await reg.call_tool(
        "artifact_read",
        {"deliverable_id": str(uuid.uuid4())},
        ctx,
    )
    assert out.body == ""
    assert out.error is not None
    assert "not found" in out.error


# ── deliverable_report dispatch ───────────────────────────────────


@pytest.mark.asyncio
async def test_deliverable_report_persists_and_emits_audit(db_session, mock_tenant_id, seeded_tenant, monkeypatch):
    project = Project(tenant_id=mock_tenant_id, name="t")
    db_session.add(project)
    await db_session.flush()
    request = Request(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        intent_summary="x",
        status=RequestStatus.open,
    )
    db_session.add(request)
    await db_session.flush()
    run = ExecutionRun(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        request_id=request.id,
        status=RunStatus.running,
        priority=RunPriority.medium,
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)

    captured: list[Any] = []

    async def fake_emit(event, *, session):
        captured.append(event)

    monkeypatch.setattr("backend.src.mcp.api.safe_emit", fake_emit)

    reg = _registry()
    ctx = _ctx_for(
        db=db_session,
        tenant_id=mock_tenant_id,
        run_id=run.id,
        project_id=project.id,
    )

    out = await reg.call_tool(
        "deliverable_report",
        {
            "title": "login screen scaffold",
            "body": "```tsx\nexport default function Login() {}\n```",
            "links": ["https://repo/PR/1"],
        },
        ctx,
    )
    await db_session.commit()
    assert out.deliverable_id  # uuid string

    assert len(captured) == 1
    assert captured[0].event_type == "nexus.deliverable.created"


# ── knowledge_search dispatch ─────────────────────────────────────


@pytest.mark.asyncio
async def test_knowledge_search_noop_returns_empty_when_no_client(
    db_session, mock_tenant_id, seeded_tenant, monkeypatch
):
    """When BSage isn't configured the integration snapshot returns the
    Noop client; the tool returns an empty hits list (never raises so
    claude can keep going in degraded mode)."""
    from backend.src.core.composer.knowledge_client import NoopKnowledgeClient

    async def fake_snapshot(session, tenant_id):
        return MagicMock(bsage=None)

    monkeypatch.setattr(
        "backend.src.core.integrations.get_tenant_integration_snapshot",
        fake_snapshot,
    )
    monkeypatch.setattr(
        "backend.src.core.composer.resolve_knowledge_client",
        lambda cfg: NoopKnowledgeClient(),
    )

    reg = _registry()
    ctx = _ctx_for(db=db_session, tenant_id=mock_tenant_id)

    out = await reg.call_tool(
        "knowledge_search",
        {"query": "auth flow", "top_k": 5},
        ctx,
    )
    assert out.hits == []


@pytest.mark.asyncio
async def test_knowledge_search_calls_configured_client(db_session, mock_tenant_id, seeded_tenant, monkeypatch):
    from backend.src.core.composer.knowledge_client import KnowledgeFragment

    class _FakeKnowledge:
        async def search(self, query: str, *, top_k: int = 10):
            assert query == "auth flow"
            return [KnowledgeFragment(title="auth.md", excerpt="JWT", path="auth.md", score=0.9)]

    async def fake_snapshot(session, tenant_id):
        return MagicMock(bsage=MagicMock())

    monkeypatch.setattr(
        "backend.src.core.integrations.get_tenant_integration_snapshot",
        fake_snapshot,
    )
    monkeypatch.setattr(
        "backend.src.core.composer.resolve_knowledge_client",
        lambda cfg: _FakeKnowledge(),
    )

    reg = _registry()
    ctx = _ctx_for(db=db_session, tenant_id=mock_tenant_id)

    out = await reg.call_tool(
        "knowledge_search",
        {"query": "auth flow", "top_k": 10},
        ctx,
    )
    assert len(out.hits) == 1
    assert out.hits[0].title == "auth.md"
    assert out.hits[0].excerpt == "JWT"
