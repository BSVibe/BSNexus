"""Phase Audit Batch 2 — `nexus.*` audit emit coverage.

Pins the contract that every domain mutation surface threads its
matching audit event into ``audit_outbox``:

* ``nexus.project.created`` — POST /api/v1/projects
* ``nexus.run.started/completed/blocked`` — RunStateMachine.transition()
* ``nexus.request.created`` — RequestExtractor when a Request row is born
* ``nexus.deliverable.created`` — _ensure_deliverable
* ``nexus.decision.created`` — dispatcher's ask_founder branch
* ``nexus.decision.resolved`` — POST /api/v1/decisions/{id}/resolve

Each test asserts:

1. The exact ``event_type`` is present in ``audit_outbox`` (no row leaks
   under a wrong namespace).
2. The actor type matches the design (user / orchestrator / system).
3. ``tenant_id`` is filled — empty tenant_id would leak into a
   cross-tenant audit query later.
4. The resource ref points to the correct row.
5. ``safe_emit`` swallows AuditEmitter exceptions so the domain path
   keeps working when the audit channel is unhealthy.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from bsvibe_audit import AuditOutboxRecord
from sqlalchemy import select

from backend.src.core import audit as audit_pkg
from backend.src.core.audit import (
    actor_from_user,
    actor_orchestrator,
    actor_system,
    resource_decision,
    resource_deliverable,
    resource_project,
    resource_request,
    resource_run,
    safe_emit,
)
from backend.src.core.state_machine import RunStateMachine
from backend.src.models import (
    ConversationMessage,
    ExecutionRun,
    Project,
    Request,
    RequestStatus,
    RunStatus,
)


async def _outbox_rows(db_session) -> list[AuditOutboxRecord]:
    rows = (await db_session.execute(select(AuditOutboxRecord))).scalars().all()
    return list(rows)


async def _types_in_outbox(db_session) -> list[str]:
    return [r.event_type for r in await _outbox_rows(db_session)]


# ── Lifespan / OutboxRelay wiring ───────────────────────────────────


def test_build_relay_disabled_when_audit_url_empty():
    """Dev environments don't set ``BSVIBE_AUTH_AUDIT_URL``. The relay
    must return a no-op singleton — ``start()`` / ``stop()`` are still
    callable but no background task runs."""
    from bsvibe_audit import AuditSettings

    cfg = AuditSettings(bsvibe_auth_audit_url="", bsvibe_auth_audit_service_token="")
    relay = audit_pkg.build_relay(settings=cfg, session_factory=None)
    assert relay.is_running() is False


@pytest.mark.asyncio
async def test_build_relay_enabled_when_audit_url_set():
    """When the URL is configured the relay's ``_enabled`` flag flips
    on. We don't actually start it (would race against the test loop's
    teardown) — just assert the wiring."""
    from bsvibe_audit import AuditSettings
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite://")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    cfg = AuditSettings(
        bsvibe_auth_audit_url="https://auth.bsvibe.dev/api/audit/events",
        bsvibe_auth_audit_service_token="dev",
    )
    relay = audit_pkg.build_relay(settings=cfg, session_factory=factory)
    assert relay._enabled is True


# ── Helper unit tests ───────────────────────────────────────────────


def test_actor_from_user_handles_missing_id_with_system_fallback():
    user = SimpleNamespace(id=None, email=None)
    actor = actor_from_user(user)
    assert actor.type == "system"
    assert actor.id == "bsnexus"


def test_actor_from_user_propagates_email_when_present():
    user = SimpleNamespace(id="u-1", email="a@b.test")
    actor = actor_from_user(user)
    assert actor.type == "user"
    assert actor.id == "u-1"
    assert actor.email == "a@b.test"


def test_actor_orchestrator_is_service_type():
    actor = actor_orchestrator()
    assert actor.type == "service"
    assert actor.id == "bsnexus.orchestrator"


def test_actor_system_is_system_type():
    assert actor_system().type == "system"


def test_resource_helpers_serialize_uuid_to_str():
    pid = uuid.uuid4()
    assert resource_project(pid).id == str(pid)
    assert resource_request(pid).id == str(pid)
    assert resource_run(pid).id == str(pid)
    assert resource_deliverable(pid).id == str(pid)
    assert resource_decision(pid).id == str(pid)


# ── safe_emit semantics ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_safe_emit_propagates_to_outbox_when_emit_succeeds(db_session):
    from bsvibe_audit.events.nexus import ProjectCreated

    event = ProjectCreated(
        actor=actor_system(),
        tenant_id="t-1",
        resource=resource_project(uuid.uuid4()),
        data={"name": "x"},
    )
    await safe_emit(event, session=db_session)
    await db_session.commit()

    types = await _types_in_outbox(db_session)
    assert types == ["nexus.project.created"]


@pytest.mark.asyncio
async def test_safe_emit_swallows_emit_exception(monkeypatch, db_session):
    from bsvibe_audit.events.nexus import ProjectCreated

    raising_emit = AsyncMock(side_effect=RuntimeError("audit broken"))
    monkeypatch.setattr(
        audit_pkg.emitter._emitter,
        "emit",
        raising_emit,
    )

    event = ProjectCreated(
        actor=actor_system(),
        tenant_id="t-1",
        resource=resource_project(uuid.uuid4()),
        data={"name": "x"},
    )
    # Must not raise.
    await safe_emit(event, session=db_session)
    raising_emit.assert_awaited_once()


# ── nexus.project.created via API ──────────────────────────────────


@pytest.mark.asyncio
async def test_create_project_emits_nexus_project_created(client, db_session, mock_tenant_id):
    resp = await client.post(
        "/api/v1/projects",
        json={"name": "Audit Demo", "description": "for the outbox"},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 201, resp.text
    project_id = resp.json()["id"]

    rows = await _outbox_rows(db_session)
    project_rows = [r for r in rows if r.event_type == "nexus.project.created"]
    assert len(project_rows) == 1
    payload = project_rows[0].payload
    assert payload["actor"]["type"] == "user"
    assert payload["tenant_id"] == str(mock_tenant_id)
    assert payload["resource"]["type"] == "project"
    assert payload["resource"]["id"] == project_id
    assert payload["data"]["name"] == "Audit Demo"


# ── nexus.run.* via state machine ──────────────────────────────────


def _seed_run(db_session, tenant_id, project_id, request_id) -> ExecutionRun:
    run = ExecutionRun(
        tenant_id=tenant_id,
        project_id=project_id,
        request_id=request_id,
        status=RunStatus.pending,
    )
    db_session.add(run)
    return run


async def _seed_full_request_chain(db_session, mock_tenant_id, seeded_tenant):
    """Insert a Project + Request so the FK-backed ExecutionRun fixture
    can be built. The dance is necessary because the founder-metaphor
    migration made all of these tenant-FK + project-FK tied."""
    project = Project(tenant_id=mock_tenant_id, name="AuditTest")
    db_session.add(project)
    await db_session.flush()

    msg = ConversationMessage(project_id=project.id, role="user", content="x")
    db_session.add(msg)
    await db_session.flush()

    req = Request(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        origin_message_id=msg.id,
        intent_summary="test",
        status=RequestStatus.open,
    )
    db_session.add(req)
    await db_session.flush()

    return project, req


@pytest.mark.asyncio
async def test_run_state_machine_emits_run_started_and_run_completed(db_session, mock_tenant_id, seeded_tenant):
    project, req = await _seed_full_request_chain(db_session, mock_tenant_id, seeded_tenant)
    run = _seed_run(db_session, mock_tenant_id, project.id, req.id)
    await db_session.flush()

    sm = RunStateMachine()
    await sm.transition(run, RunStatus.running, actor="orchestrator", db_session=db_session)
    await sm.transition(run, RunStatus.done, actor="orchestrator", db_session=db_session)
    await db_session.commit()

    types = await _types_in_outbox(db_session)
    assert "nexus.run.started" in types
    assert "nexus.run.completed" in types
    # No spurious blocked or pending event.
    assert "nexus.run.blocked" not in types

    # Started event payload pins the orchestrator actor + run resource.
    started = [r for r in await _outbox_rows(db_session) if r.event_type == "nexus.run.started"][0]
    assert started.payload["actor"]["type"] == "service"
    assert started.payload["actor"]["id"] == "bsnexus.orchestrator"
    assert started.payload["resource"]["id"] == str(run.id)
    assert started.payload["data"]["from_status"] == "pending"
    assert started.payload["data"]["to_status"] == "running"


@pytest.mark.asyncio
async def test_run_state_machine_emits_run_blocked_with_reason(db_session, mock_tenant_id, seeded_tenant):
    project, req = await _seed_full_request_chain(db_session, mock_tenant_id, seeded_tenant)
    run = _seed_run(db_session, mock_tenant_id, project.id, req.id)
    await db_session.flush()

    sm = RunStateMachine()
    await sm.transition(run, RunStatus.running, actor="orchestrator", db_session=db_session)
    await sm.transition(
        run,
        RunStatus.blocked,
        actor="orchestrator",
        db_session=db_session,
        reason="executor timeout",
    )
    await db_session.commit()

    rows = [r for r in await _outbox_rows(db_session) if r.event_type == "nexus.run.blocked"]
    assert len(rows) == 1
    assert rows[0].payload["data"]["reason"] == "executor timeout"


@pytest.mark.asyncio
async def test_run_state_machine_skips_audit_when_db_session_is_none():
    """Bare-namespace state-machine tests (no DB) must keep working —
    we lock that contract explicitly so future audit additions don't
    inadvertently bring DB requirements into pure-logic tests."""
    sm = RunStateMachine()
    run = SimpleNamespace(
        id="run-id",
        project_id="proj-id",
        request_id="req-id",
        tenant_id="t-1",
        status=RunStatus.pending,
        error_message=None,
        started_at=None,
        completed_at=None,
    )
    # No DB, no stream — still works without raising.
    await sm.transition(run, RunStatus.running, db_session=None, stream_manager=None)
    assert run.status == RunStatus.running


@pytest.mark.asyncio
async def test_run_state_machine_does_not_emit_on_pending_retry(db_session, mock_tenant_id, seeded_tenant):
    project, req = await _seed_full_request_chain(db_session, mock_tenant_id, seeded_tenant)
    run = _seed_run(db_session, mock_tenant_id, project.id, req.id)
    await db_session.flush()

    sm = RunStateMachine()
    await sm.transition(run, RunStatus.running, actor="orchestrator", db_session=db_session)
    await sm.transition(
        run,
        RunStatus.blocked,
        actor="orchestrator",
        db_session=db_session,
        reason="x",
    )
    # Retry transition: blocked → pending. Should NOT emit a 4th event.
    await sm.transition(run, RunStatus.pending, actor="orchestrator", db_session=db_session)
    await db_session.commit()

    types = await _types_in_outbox(db_session)
    # started + blocked = 2; the pending retry adds zero.
    nexus_run_events = [t for t in types if t.startswith("nexus.run.")]
    assert sorted(nexus_run_events) == ["nexus.run.blocked", "nexus.run.started"]


# ── nexus.request.created via conversation API inline rule ─────────
# Coverage moved to test_conversation_api.py — the inline rule that
# replaced RequestExtractor lives in api/conversation.send_message.


# ── nexus.deliverable.created via run_artifacts ─────────────────────


@pytest.mark.asyncio
async def test_publish_run_output_emits_deliverable_created(db_session, mock_tenant_id, seeded_tenant):
    """A completed run with output materialises a ``Deliverable`` row
    AND a ``nexus.deliverable.created`` outbox row."""
    from backend.src.core.run_artifacts import publish_run_output
    from backend.src.models import RunPriority

    project = Project(tenant_id=mock_tenant_id, name="DelivTest")
    db_session.add(project)
    await db_session.flush()

    req = Request(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        intent_summary="ship something",
        status=RequestStatus.open,
    )
    db_session.add(req)
    await db_session.flush()

    run = ExecutionRun(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        request_id=req.id,
        status=RunStatus.done,
        priority=RunPriority.medium,
        output_ref={"inline": "Hello world.", "files": [{"path": "a.py", "size": 5}]},
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)

    await publish_run_output(run, db_session)
    await db_session.commit()

    rows = [r for r in await _outbox_rows(db_session) if r.event_type == "nexus.deliverable.created"]
    assert len(rows) == 1
    payload = rows[0].payload
    assert payload["actor"]["type"] == "service"
    assert payload["actor"]["id"] == "bsnexus.orchestrator"
    assert payload["resource"]["type"] == "deliverable"
    assert payload["data"]["run_id"] == str(run.id)
    assert payload["data"]["request_id"] == str(req.id)
    assert payload["data"]["file_count"] == 1


@pytest.mark.asyncio
async def test_publish_run_output_no_files_no_inline_emits_nothing(db_session, mock_tenant_id, seeded_tenant):
    """Empty runs don't materialise a Deliverable, so they don't emit
    ``nexus.deliverable.created`` either — the audit stays silent in
    lockstep with the domain."""
    from backend.src.core.run_artifacts import publish_run_output
    from backend.src.models import RunPriority

    project = Project(tenant_id=mock_tenant_id, name="EmptyRun")
    db_session.add(project)
    await db_session.flush()

    req = Request(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        intent_summary="empty",
        status=RequestStatus.open,
    )
    db_session.add(req)
    await db_session.flush()

    run = ExecutionRun(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        request_id=req.id,
        status=RunStatus.done,
        priority=RunPriority.medium,
        output_ref={"inline": "", "files": []},
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)

    await publish_run_output(run, db_session)
    await db_session.commit()

    types = await _types_in_outbox(db_session)
    assert "nexus.deliverable.created" not in types


# ── nexus.decision.created via direct emit ─────────────────────────


@pytest.mark.asyncio
async def test_decision_created_payload_shape_via_direct_emit(db_session, mock_tenant_id):
    """The dispatcher's ask_founder branch is exercised end-to-end in
    test_dispatch_executor_guard.py; here we just pin the audit payload
    shape via a direct emit so future Decision schema changes surface
    in this contract test."""
    from bsvibe_audit.events.nexus import DecisionCreated

    decision_id = uuid.uuid4()
    request_id = uuid.uuid4()
    run_id = uuid.uuid4()
    project_id = uuid.uuid4()

    await safe_emit(
        DecisionCreated(
            actor=actor_orchestrator(),
            tenant_id=str(mock_tenant_id),
            resource=resource_decision(decision_id),
            data={
                "project_id": str(project_id),
                "request_id": str(request_id),
                "origin_run_id": str(run_id),
                "question": "go or no go?",
                "blocking": True,
                "option_count": 2,
            },
        ),
        session=db_session,
    )
    await db_session.commit()

    rows = [r for r in await _outbox_rows(db_session) if r.event_type == "nexus.decision.created"]
    assert len(rows) == 1
    payload = rows[0].payload
    assert payload["actor"]["type"] == "service"
    assert payload["resource"]["id"] == str(decision_id)
    assert payload["data"]["blocking"] is True
    assert payload["data"]["question"] == "go or no go?"


# ── nexus.decision.resolved via API ────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_decision_emits_decision_resolved(client, db_session, mock_tenant_id, seeded_tenant):
    from backend.src.models import Decision, Project

    project = Project(tenant_id=mock_tenant_id, name="DecideTest")
    db_session.add(project)
    await db_session.flush()

    decision = Decision(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        request_id=None,
        origin_run_id=None,
        question="A or B?",
        options=["A", "B"],
        blocking=True,
    )
    db_session.add(decision)
    await db_session.commit()

    resp = await client.post(
        f"/api/v1/decisions/{decision.id}/resolve",
        json={"resolution": "A", "resolved_by": "founder"},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200, resp.text

    rows = [r for r in await _outbox_rows(db_session) if r.event_type == "nexus.decision.resolved"]
    assert len(rows) == 1
    payload = rows[0].payload
    assert payload["actor"]["type"] == "user"
    assert payload["resource"]["type"] == "decision"
    assert payload["resource"]["id"] == str(decision.id)
    assert payload["data"]["resolution"] == "A"
    assert payload["data"]["resolved_by"] == "founder"
    assert payload["data"]["blocking"] is True
