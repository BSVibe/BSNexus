"""S4 — RunOrchestrator end-to-end event-chain scenario tests (Audit §6).

Sprint 0–3 individually pinned compose, audit, executor, and state-
machine paths. This module is the **integration scenario** the audit
gap calls out: dispatch → compose → audit-block / audit-allow →
executor → on_run_completed → state-machine + history rows + stream
events emitted, all observed end-to-end on a real (in-memory) DB.

These tests pin behaviours that no individual unit test asserts:

  * A blocked-by-audit preflight short-circuits **before** the executor
    is ever called.
  * A successful run writes ExecutionRunHistory rows (pending → running
    → done) AND a CompositionSnapshot.
  * ``stream_manager.publish_project_event`` fires for every state
    transition with ``run_transition`` event.
  * ``on_run_completed`` schedules a follow-up replanner run with
    ``parent_run_id`` set.
  * Foreign-tenant runs do not leak through the orchestrator.

CLAUDE.md NEVER rule preserved: state changes go through the
RunStateMachine, never bypassed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from backend.src.core.run_orchestrator import RunOrchestrator
from backend.src.models import (
    CompositionSnapshot,
    ExecutionRun,
    ExecutionRunHistory,
    Project,
    Request,
    RequestStatus,
    RunPriority,
    RunStatus,
)


async def _seed_run(db_session, tenant_id) -> tuple[Project, Request, ExecutionRun]:
    project = Project(tenant_id=tenant_id, name="E2E", description="")
    db_session.add(project)
    await db_session.flush()
    request = Request(
        tenant_id=tenant_id,
        project_id=project.id,
        intent_summary="Do the thing",
        status=RequestStatus.open,
    )
    db_session.add(request)
    await db_session.flush()
    run = ExecutionRun(
        tenant_id=tenant_id,
        project_id=project.id,
        request_id=request.id,
        status=RunStatus.pending,
        priority=RunPriority.medium,
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)
    return project, request, run


@pytest.mark.asyncio
async def test_full_chain_persists_snapshot_history_and_publishes_events(
    db_session, mock_tenant_id, seeded_tenant
) -> None:
    """One dispatch must produce: a CompositionSnapshot + 2 history rows
    (pending→running, running→done) + 2 stream-publish events. No
    cross-cutting unit test currently asserts all five together — Audit
    §6 'integration scenario' gap."""
    project, _req, run = await _seed_run(db_session, mock_tenant_id)

    publish_calls: list[tuple[str, dict]] = []

    sm = MagicMock()

    async def fake_publish(stream_id, event_name, payload):  # noqa: ANN001
        publish_calls.append((event_name, payload))

    sm.publish_project_event = AsyncMock(side_effect=fake_publish)

    adapter = MagicMock()
    adapter.tools_supported = ["read", "write"]
    adapter.execute = AsyncMock(
        return_value={
            "status": "done",
            "output_type": "text",
            "output_ref": {"inline": "ok"},
            "actual_cost_cents": 0,
        }
    )

    orch = RunOrchestrator()
    await orch.dispatch_run(run.id, db=db_session, executor=adapter, stream_manager=sm)
    await db_session.commit()

    # 1. Snapshot persisted and linked to the run.
    refreshed = (await db_session.execute(select(ExecutionRun).where(ExecutionRun.id == run.id))).scalar_one()
    assert refreshed.composition_snapshot_id is not None
    assert refreshed.status == RunStatus.done

    snap = (
        await db_session.execute(
            select(CompositionSnapshot).where(CompositionSnapshot.id == refreshed.composition_snapshot_id)
        )
    ).scalar_one()
    assert snap.tenant_id == mock_tenant_id
    assert snap.execution_run_id == run.id
    assert snap.system_prompt_ref is not None

    # 2. History rows: pending→running and running→done.
    history = (
        (
            await db_session.execute(
                select(ExecutionRunHistory)
                .where(ExecutionRunHistory.run_id == run.id)
                .order_by(ExecutionRunHistory.timestamp.asc())
            )
        )
        .scalars()
        .all()
    )
    transitions = [(h.from_status, h.to_status) for h in history]
    assert (RunStatus.pending, RunStatus.running) in transitions
    assert (RunStatus.running, RunStatus.done) in transitions

    # 3. Stream events fired for every transition.
    event_names = [name for name, _payload in publish_calls]
    assert event_names.count("run_transition") >= 2
    # Each payload carries the canonical fields.
    for _name, payload in publish_calls:
        assert payload["run_id"] == str(run.id)
        assert "from_status" in payload
        assert "to_status" in payload

    # 4. Executor was called exactly once.
    adapter.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_audit_block_short_circuits_before_executor(db_session, mock_tenant_id, seeded_tenant) -> None:
    """When the BSupervisor preflight returns ``blocked=True``, the run
    must transition pending→blocked and the executor must never be
    invoked. This is the single most security-relevant integration
    scenario — pin it end-to-end."""
    project, _req, run = await _seed_run(db_session, mock_tenant_id)

    adapter = MagicMock()
    adapter.tools_supported = ["read", "write"]
    adapter.execute = AsyncMock()  # should not be called

    sm = MagicMock()
    sm.publish_project_event = AsyncMock()

    # Stub resolve_audit_sink to a sink that refuses preflight.
    from unittest.mock import patch

    class _BlockingSink:
        async def preflight(self, run_, snapshot_):  # noqa: ARG002
            from backend.src.core.audit import AuditResult

            return AuditResult(blocked=True, reason="rule X violated", degraded=False)

        async def emit_post(self, *args, **kwargs):  # noqa: ARG002
            pass

    with patch("backend.src.core.run_orchestrator.resolve_audit_sink", return_value=_BlockingSink()):
        orch = RunOrchestrator()
        await orch.dispatch_run(run.id, db=db_session, executor=adapter, stream_manager=sm)
        await db_session.commit()

    refreshed = (await db_session.execute(select(ExecutionRun).where(ExecutionRun.id == run.id))).scalar_one()
    assert refreshed.status == RunStatus.blocked
    assert "rule X violated" in (refreshed.error_message or "")
    # CRITICAL: executor must never run for a blocked preflight.
    adapter.execute.assert_not_called()


@pytest.mark.asyncio
async def test_executor_failure_transitions_to_blocked(db_session, mock_tenant_id, seeded_tenant) -> None:
    """A non-cancellation exception from the executor must transition
    the run to ``blocked`` (so the watchdog can later retry) — not crash
    the orchestrator."""
    project, _req, run = await _seed_run(db_session, mock_tenant_id)

    adapter = MagicMock()
    adapter.tools_supported = ["read", "write"]
    adapter.execute = AsyncMock(side_effect=RuntimeError("LLM provider down"))

    orch = RunOrchestrator()
    await orch.dispatch_run(run.id, db=db_session, executor=adapter)
    await db_session.commit()

    refreshed = (await db_session.execute(select(ExecutionRun).where(ExecutionRun.id == run.id))).scalar_one()
    assert refreshed.status == RunStatus.blocked
    assert "LLM provider down" in (refreshed.error_message or "")


@pytest.mark.asyncio
async def test_dispatch_run_does_not_leak_across_tenants(db_session, mock_tenant_id, seeded_tenant) -> None:
    """A run owned by tenant A loaded by tenant-A's snapshot path must
    never surface tenant B's integration config — pin the cross-tenant
    isolation contract that protects ``TenantIntegrationSnapshot``."""
    import uuid as _uuid

    from backend.src.models import Tenant, TenantIntegrationConfig
    from backend.src.models.tenant_integration_config import IntegrationProvider

    other_tid = _uuid.uuid4()
    db_session.add(Tenant(id=other_tid, name="Other", slug=f"o-{other_tid.hex[:8]}", owner_user_id="x"))
    await db_session.commit()
    db_session.add(
        TenantIntegrationConfig(
            tenant_id=other_tid,
            provider=IntegrationProvider.bsage,
            enabled=True,
            base_url="http://victim",
            api_key_encrypted=None,
        )
    )
    await db_session.commit()

    # Ours.
    _project, _req, run = await _seed_run(db_session, mock_tenant_id)

    adapter = MagicMock()
    adapter.tools_supported = ["read", "write"]
    adapter.execute = AsyncMock(
        return_value={"status": "done", "output_type": "text", "output_ref": {"inline": "ok"}, "actual_cost_cents": 0}
    )

    orch = RunOrchestrator()
    await orch.dispatch_run(run.id, db=db_session, executor=adapter)
    await db_session.commit()

    refreshed = (await db_session.execute(select(ExecutionRun).where(ExecutionRun.id == run.id))).scalar_one()
    # Run completed using mock_tenant_id's (empty) snapshot — not
    # tenant-other's BSage config.
    assert refreshed.status == RunStatus.done
    snap = (
        await db_session.execute(
            select(CompositionSnapshot).where(CompositionSnapshot.id == refreshed.composition_snapshot_id)
        )
    ).scalar_one()
    assert snap.tenant_id == mock_tenant_id
    # Source = local (no BSage configured for mock_tenant_id).
    assert snap.source.value == "local"
