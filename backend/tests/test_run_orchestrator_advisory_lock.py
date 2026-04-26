"""S3-1 — PG advisory lock for RunOrchestrator horizontal scaling.

When BSNexus runs as multiple uvicorn instances (autoscaled, or
red/blue deploy with overlap), two instances can race to dispatch the
same ExecutionRun. Without coordination, both call into compose →
audit → executor and we end up with double LLM cost and a corrupted
``ExecutionRun`` state machine.

The fix is a Postgres advisory lock keyed by ``hash(run_id)`` taken at
the entry of ``RunOrchestrator.dispatch_run``. The first caller acquires
the lock and proceeds; the second caller's ``pg_try_advisory_lock``
returns False and the call returns immediately as a no-op (the run is
left in whatever state the active dispatcher will move it to).

Locks are session-scoped (``pg_try_advisory_lock``) and explicitly
released at the end of dispatch — but if the connection dies they're
auto-released by Postgres at backend disconnect, so a crashed dispatcher
never wedges a run permanently.

Tests run on SQLite (the standard test backend). The advisory_lock
helper transparently falls back to a process-local ``asyncio.Lock``
indexed by ``run_id`` so the same coordination semantics apply within a
single process — that's what we exercise here. The PG-specific
``pg_try_advisory_lock`` path is exercised by the fresh-PG migration
suite (the helper is wired through SQLAlchemy ``select(func.…)``).
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from backend.src.core.advisory_lock import (
    advisory_key_for_run,
    release_run_dispatch_lock,
    try_run_dispatch_lock,
)
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
    project = Project(tenant_id=tenant_id, name="Lock test", description="")
    db_session.add(project)
    await db_session.flush()

    request = Request(
        tenant_id=tenant_id,
        project_id=project.id,
        intent_summary="lock me",
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


# -- advisory_key_for_run ----------------------------------------------------


def test_advisory_key_for_run_is_deterministic_64bit_int() -> None:
    """The PG advisory lock takes a bigint key. The helper must derive
    a stable 64-bit signed integer from a UUID (Postgres rejects values
    outside ``[-9223372036854775808, 9223372036854775807]``)."""
    run_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    a = advisory_key_for_run(run_id)
    b = advisory_key_for_run(run_id)
    assert a == b
    assert isinstance(a, int)
    assert -(2**63) <= a <= (2**63) - 1


def test_advisory_key_for_run_differs_across_run_ids() -> None:
    a = advisory_key_for_run(uuid.uuid4())
    b = advisory_key_for_run(uuid.uuid4())
    assert a != b


# -- try / release lock primitives -------------------------------------------


@pytest.mark.asyncio
async def test_try_run_dispatch_lock_in_process_acquires_then_blocks(db_session) -> None:
    """First call gets the lock; second concurrent call (same run_id)
    must observe ``acquired=False`` until the first releases."""
    run_id = uuid.uuid4()

    first = await try_run_dispatch_lock(db_session, run_id)
    assert first is True

    second = await try_run_dispatch_lock(db_session, run_id)
    assert second is False, "second concurrent acquire must fail-fast"

    await release_run_dispatch_lock(db_session, run_id)

    third = await try_run_dispatch_lock(db_session, run_id)
    assert third is True
    await release_run_dispatch_lock(db_session, run_id)


@pytest.mark.asyncio
async def test_try_run_dispatch_lock_different_runs_dont_collide(db_session) -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    assert await try_run_dispatch_lock(db_session, a) is True
    assert await try_run_dispatch_lock(db_session, b) is True
    await release_run_dispatch_lock(db_session, a)
    await release_run_dispatch_lock(db_session, b)


@pytest.mark.asyncio
async def test_release_run_dispatch_lock_is_idempotent(db_session) -> None:
    run_id = uuid.uuid4()
    await try_run_dispatch_lock(db_session, run_id)
    # Releasing twice must not raise.
    await release_run_dispatch_lock(db_session, run_id)
    await release_run_dispatch_lock(db_session, run_id)


# -- RunOrchestrator integration --------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_run_acquires_and_releases_lock(db_session, mock_tenant_id, seeded_tenant) -> None:
    """Happy path: a normal dispatch acquires the lock, completes, then
    releases — so a follow-up dispatch on the same run isn't blocked by
    a stale lock."""
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

    # Lock must be released after dispatch completes — verify by
    # acquiring from a fresh session (in-process fallback shares state
    # but PG would have released at session close).
    assert await try_run_dispatch_lock(db_session, run.id) is True
    await release_run_dispatch_lock(db_session, run.id)

    refreshed = (await db_session.execute(select(ExecutionRun).where(ExecutionRun.id == run.id))).scalar_one()
    assert refreshed.status == RunStatus.done


@pytest.mark.asyncio
async def test_concurrent_dispatch_on_same_run_only_one_executes(
    db_session, test_session_maker, mock_tenant_id, seeded_tenant
) -> None:
    """Two dispatchers race on the same run. Exactly one calls the
    executor; the other returns early as a no-op — the run is left in
    whatever state the winning dispatcher set."""
    run = await _seed_run(db_session, mock_tenant_id)

    started = asyncio.Event()
    proceed = asyncio.Event()
    call_count = {"n": 0}

    async def slow_execute(*args, **kwargs):
        call_count["n"] += 1
        started.set()
        await proceed.wait()
        return {
            "status": "done",
            "output_type": "text",
            "output_ref": {"inline": "ok"},
            "actual_cost_cents": 0,
        }

    adapter = MagicMock()
    adapter.tools_supported = ["read", "write"]
    adapter.execute = AsyncMock(side_effect=slow_execute)

    orch = RunOrchestrator()

    async def first():
        async with test_session_maker() as s:
            return await orch.dispatch_run(run.id, db=s, executor=adapter)

    async def second():
        # Wait until first is inside the lock (executor entered).
        await started.wait()
        async with test_session_maker() as s:
            return await orch.dispatch_run(run.id, db=s, executor=adapter)

    t1 = asyncio.create_task(first())
    t2 = asyncio.create_task(second())

    # Let second land and bail out.
    await asyncio.sleep(0.05)
    proceed.set()

    await asyncio.gather(t1, t2)

    # Exactly one execution.
    assert call_count["n"] == 1, f"expected 1 execute, got {call_count['n']}"


@pytest.mark.asyncio
async def test_dispatch_run_releases_lock_on_executor_failure(db_session, mock_tenant_id, seeded_tenant) -> None:
    """A run that errors out during executor must still release the
    advisory lock — otherwise the run is wedged forever on retry."""
    run = await _seed_run(db_session, mock_tenant_id)

    adapter = MagicMock()
    adapter.tools_supported = ["read", "write"]
    adapter.execute = AsyncMock(side_effect=RuntimeError("boom"))

    orch = RunOrchestrator()
    await orch.dispatch_run(run.id, db=db_session, executor=adapter)
    await db_session.commit()

    # The lock must be released after the failure — re-acquire is OK.
    assert await try_run_dispatch_lock(db_session, run.id) is True
    await release_run_dispatch_lock(db_session, run.id)
