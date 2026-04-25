"""Orchestrator keeps a run in ``running`` state when the executor
returns ``status="dispatched"`` (async worker path).

This is the key difference between in-process executors (synchronous,
return final output → transition to ``done``) and worker executors
(asynchronous, publish-and-forget → result lands on a Redis stream
later and a separate consumer finalizes).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from backend.src.core.run_orchestrator import RunOrchestrator
from backend.src.models import (
    ExecutionRun,
    Project,
    Request,
    RequestStatus,
    RunPriority,
    RunStatus,
)


async def _seed_run(db_session, tenant_id) -> ExecutionRun:
    project = Project(tenant_id=tenant_id, name="Worker test", description="")
    db_session.add(project)
    await db_session.flush()

    request = Request(
        tenant_id=tenant_id,
        project_id=project.id,
        intent_summary="Implement a hello-world script",
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
    return run


@pytest.mark.asyncio
async def test_dispatched_result_leaves_run_in_running(db_session, mock_tenant_id, seeded_tenant):
    run = await _seed_run(db_session, mock_tenant_id)

    # Fake adapter that returns the dispatched sentinel
    adapter = MagicMock()
    adapter.tools_supported = ["read", "write"]
    adapter.execute = AsyncMock(
        return_value={
            "status": "dispatched",
            "output_type": None,
            "output_ref": None,
            "worker_id": str(uuid.uuid4()),
            "stream_msg_id": "stream-id-1",
        }
    )

    orch = RunOrchestrator()
    await orch.dispatch_run(run.id, db=db_session, executor=adapter)
    await db_session.commit()

    # The orchestrator should have transitioned pending → running but
    # NOT advanced to done; the worker will finalize later.
    refreshed = (await db_session.execute(select(ExecutionRun).where(ExecutionRun.id == run.id))).scalar_one()
    assert refreshed.status == RunStatus.running
    assert refreshed.completed_at is None
    # And a composition snapshot was persisted along the way.
    assert refreshed.composition_snapshot_id is not None


@pytest.mark.asyncio
async def test_done_result_still_transitions_to_done(db_session, mock_tenant_id, seeded_tenant):
    """Synchronous executors returning a regular result dict keep the
    existing happy path intact — the orchestrator marks them done."""
    run = await _seed_run(db_session, mock_tenant_id)

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
    await orch.dispatch_run(run.id, db=db_session, executor=adapter)
    await db_session.commit()

    refreshed = (await db_session.execute(select(ExecutionRun).where(ExecutionRun.id == run.id))).scalar_one()
    assert refreshed.status == RunStatus.done
