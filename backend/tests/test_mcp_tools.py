"""Tests for ``core.mcp.tools`` — the run-scoped tool implementations
the MCP server exposes to the BSGateway worker's claude CLI.

These are tested as plain async functions (not via the MCP protocol)
so we can pin the DB / queue / cross-tenant guards without fighting
SSE plumbing. The MCP wire layer is a thin wrapper over these.

Scope: v1 ships ``decision.create``, ``decision.wait``, ``artifact.list``,
``knowledge.search``. ``report_deliverable`` and ``artifact.read``
involve DeliverableVersion + storage-backend round-trips and ride a
follow-up PR.
"""

from __future__ import annotations

import uuid

import pytest

from backend.src.mcp.decision_queue import DecisionQueue
from backend.src.mcp.tools import (
    MCPToolError,
    create_decision,
    list_run_artifacts,
    search_knowledge,
    wait_for_decision,
)
from backend.src.models import (
    Decision,
    Deliverable,
    DeliverableType,
    ExecutionRun,
    Project,
    Request,
    RequestStatus,
    RunPriority,
    RunStatus,
)


# ─── decision.create ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_decision_persists_row(db_session, mock_tenant_id, seeded_tenant):
    project = Project(tenant_id=mock_tenant_id, name="t")
    db_session.add(project)
    await db_session.flush()
    request = Request(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        intent_summary="do x",
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

    decision_id = await create_decision(
        question="should I use postgres or sqlite?",
        options=["postgres", "sqlite"],
        context="ambiguity in the requirements",
        run_id=run.id,
        tenant_id=mock_tenant_id,
        project_id=project.id,
        db=db_session,
    )
    await db_session.commit()

    row = await db_session.get(Decision, decision_id)
    assert row is not None
    assert row.tenant_id == mock_tenant_id
    assert row.project_id == project.id
    assert row.request_id == request.id
    assert row.origin_run_id == run.id
    assert "should I use postgres or sqlite?" in row.question
    # Context is appended into the question, not stuffed into options.
    assert "ambiguity in the requirements" in row.question
    assert row.options == ["postgres", "sqlite"]


@pytest.mark.asyncio
async def test_create_decision_rejects_wrong_tenant(db_session, mock_tenant_id, seeded_tenant):
    """A run-scoped token can only open decisions on its own run."""
    project = Project(tenant_id=mock_tenant_id, name="t")
    db_session.add(project)
    await db_session.flush()
    request = Request(
        tenant_id=mock_tenant_id, project_id=project.id, intent_summary="x", status=RequestStatus.open
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

    with pytest.raises(MCPToolError, match="tenant"):
        await create_decision(
            question="?",
            options=[],
            context=None,
            run_id=run.id,
            tenant_id=uuid.uuid4(),  # cross-tenant
            project_id=project.id,
            db=db_session,
        )


# ─── decision.wait ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_wait_for_decision_returns_resolve_payload(db_session, mock_tenant_id, seeded_tenant):
    project = Project(tenant_id=mock_tenant_id, name="t")
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

    queue = DecisionQueue()
    queue.register(decision.id)
    queue.notify(decision.id, result={"choice": "yes", "notes": "go"})

    res = await wait_for_decision(
        decision_id=decision.id,
        tenant_id=mock_tenant_id,
        queue=queue,
        timeout_seconds=1.0,
        db=db_session,
    )
    assert res == {"choice": "yes", "notes": "go"}


@pytest.mark.asyncio
async def test_wait_for_decision_already_resolved_returns_db_payload(db_session, mock_tenant_id, seeded_tenant):
    """If the founder resolved the decision before the CLI reached
    decision.wait (e.g. across a 504 timeout retry), the queue is empty
    but the DB row already has resolved_choice/notes — fall back to that
    so the second-attempt run picks up where it left off."""
    project = Project(tenant_id=mock_tenant_id, name="t")
    db_session.add(project)
    await db_session.flush()
    decision = Decision(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        question="?",
        options=[],
        blocking=True,
        resolution="yes",
    )
    db_session.add(decision)
    await db_session.commit()
    await db_session.refresh(decision)

    queue = DecisionQueue()
    res = await wait_for_decision(
        decision_id=decision.id,
        tenant_id=mock_tenant_id,
        queue=queue,
        timeout_seconds=0.5,
        db=db_session,
    )
    assert res == {"choice": "yes", "notes": ""}


@pytest.mark.asyncio
async def test_wait_for_decision_cross_tenant_rejected(db_session, mock_tenant_id, seeded_tenant):
    project = Project(tenant_id=mock_tenant_id, name="t")
    db_session.add(project)
    await db_session.flush()
    decision = Decision(
        tenant_id=mock_tenant_id, project_id=project.id, question="?", options=[], blocking=True
    )
    db_session.add(decision)
    await db_session.commit()
    await db_session.refresh(decision)

    queue = DecisionQueue()
    with pytest.raises(MCPToolError, match="tenant"):
        await wait_for_decision(
            decision_id=decision.id,
            tenant_id=uuid.uuid4(),
            queue=queue,
            timeout_seconds=0.1,
            db=db_session,
        )


# ─── artifact.list ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_run_artifacts_returns_run_deliverables(db_session, mock_tenant_id, seeded_tenant):
    project = Project(tenant_id=mock_tenant_id, name="t")
    db_session.add(project)
    await db_session.flush()
    request = Request(
        tenant_id=mock_tenant_id, project_id=project.id, intent_summary="x", status=RequestStatus.open
    )
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

    items = await list_run_artifacts(
        request_id=request.id, tenant_id=mock_tenant_id, db=db_session
    )

    assert len(items) == 1
    assert items[0]["title"] == "output.md"
    assert items[0]["type"] == "doc"
    assert items[0]["id"] == str(deliv.id)


@pytest.mark.asyncio
async def test_list_run_artifacts_cross_tenant_returns_empty(db_session, mock_tenant_id, seeded_tenant):
    """Cross-tenant queries return [] (not 404) — same defensive pattern
    as the rest of the API. Defence in depth: the SSE handler already
    rejects mismatched tokens, but tools enforce tenant scope too."""
    project = Project(tenant_id=mock_tenant_id, name="t")
    db_session.add(project)
    await db_session.flush()
    request = Request(
        tenant_id=mock_tenant_id, project_id=project.id, intent_summary="x", status=RequestStatus.open
    )
    db_session.add(request)
    await db_session.commit()
    await db_session.refresh(request)

    items = await list_run_artifacts(
        request_id=request.id, tenant_id=uuid.uuid4(), db=db_session
    )
    assert items == []


# ─── knowledge.search (BSage proxy with noop fallback) ───────────────


@pytest.mark.asyncio
async def test_search_knowledge_noop_returns_empty_when_no_client() -> None:
    """When the tenant has no BSage configured, search_knowledge
    returns [] — never raises so claude can keep going."""
    items = await search_knowledge(query="auth flow", knowledge_client=None)
    assert items == []


@pytest.mark.asyncio
async def test_search_knowledge_delegates_to_client_search() -> None:
    """A configured KnowledgeClient is invoked with the query and the
    returned fragments are surfaced to claude as title + excerpt dicts."""
    from backend.src.core.composer.knowledge_client import KnowledgeFragment

    class _FakeKnowledge:
        async def search(self, query: str, *, top_k: int = 10) -> list[KnowledgeFragment]:
            assert query == "auth flow"
            assert top_k == 10
            return [KnowledgeFragment(title="auth.md", excerpt="JWT flow", path="auth.md", score=0.9)]

    items = await search_knowledge(query="auth flow", knowledge_client=_FakeKnowledge())
    assert items == [{"title": "auth.md", "excerpt": "JWT flow"}]
