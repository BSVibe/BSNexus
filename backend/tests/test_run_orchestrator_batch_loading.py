"""S2-1 M2: regression test for N+1 batch loading in RunOrchestrator.

The previous ``_find_ready_children`` ran one DB round-trip *per
candidate* to check whether all of that candidate's dependencies were
done. With C candidates this was C+1 queries (the Audit's M2 finding,
re-classified onto RunOrchestrator after the GlobalDispatcher retired).

This test asserts the batched implementation issues at most a fixed
small number of round-trips regardless of candidate count.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.run_orchestrator import _find_ready_children
from backend.src.models import (
    ExecutionRun,
    Project,
    Request,
    RequestStatus,
    RunStatus,
    execution_run_dependencies,
)


async def _make_project(db: AsyncSession, tenant_id: uuid.UUID) -> Project:
    project = Project(
        tenant_id=tenant_id,
        name="N+1 perf project",
    )
    db.add(project)
    await db.flush()
    return project


async def _make_request(db: AsyncSession, project: Project) -> Request:
    request = Request(
        tenant_id=project.tenant_id,
        project_id=project.id,
        intent_summary="batch load test",
        status=RequestStatus.open,
    )
    db.add(request)
    await db.flush()
    return request


async def _make_run(
    db: AsyncSession,
    project: Project,
    request: Request,
    *,
    status: RunStatus,
) -> ExecutionRun:
    run = ExecutionRun(
        tenant_id=project.tenant_id,
        project_id=project.id,
        request_id=request.id,
        status=status,
    )
    db.add(run)
    await db.flush()
    return run


@pytest.mark.asyncio
async def test_find_ready_children_returns_only_runs_with_all_deps_done(
    db_session: AsyncSession,
    seeded_tenant,
):
    """Two-dep candidate: ready when *all* deps are done, blocked otherwise.

    Sets up the graph::

        parent (done) ──┐
                        ├──► child_ready   (deps: parent + extra_done)
                        ├──► child_pending (deps: parent + extra_pending)
        extra_done   ── ┘
        extra_pending

    Only ``child_ready`` should appear in the result.
    """
    project = await _make_project(db_session, seeded_tenant.id)
    request = await _make_request(db_session, project)

    parent = await _make_run(db_session, project, request, status=RunStatus.done)
    extra_done = await _make_run(db_session, project, request, status=RunStatus.done)
    extra_pending = await _make_run(db_session, project, request, status=RunStatus.pending)
    child_ready = await _make_run(db_session, project, request, status=RunStatus.blocked)
    child_pending = await _make_run(db_session, project, request, status=RunStatus.blocked)

    await db_session.execute(
        execution_run_dependencies.insert(),
        [
            {"run_id": child_ready.id, "dependency_id": parent.id},
            {"run_id": child_ready.id, "dependency_id": extra_done.id},
            {"run_id": child_pending.id, "dependency_id": parent.id},
            {"run_id": child_pending.id, "dependency_id": extra_pending.id},
        ],
    )
    await db_session.flush()

    ready = await _find_ready_children(db_session, parent)
    ready_ids = {r.id for r in ready}

    assert child_ready.id in ready_ids
    assert child_pending.id not in ready_ids


@pytest.mark.asyncio
async def test_find_ready_children_uses_batch_query_no_n_plus_one(
    db_session: AsyncSession,
    seeded_tenant,
):
    """N+1 guard: query count stays sub-linear in candidate count.

    With 8 candidate children and the previous per-candidate dep query,
    we'd expect 1 (candidates) + 1 (dependent_ids) + 8 (per-candidate
    dep statuses) = 10 round-trips. The batched implementation issues
    a single grouped query for dep statuses regardless of candidate
    count: at most 4 round-trips total (this test allows 5 for
    headroom).
    """
    project = await _make_project(db_session, seeded_tenant.id)
    request = await _make_request(db_session, project)

    parent = await _make_run(db_session, project, request, status=RunStatus.done)

    candidate_ids: list[uuid.UUID] = []
    for _ in range(8):
        candidate = await _make_run(db_session, project, request, status=RunStatus.blocked)
        candidate_ids.append(candidate.id)
        await db_session.execute(
            execution_run_dependencies.insert(),
            [{"run_id": candidate.id, "dependency_id": parent.id}],
        )
    await db_session.flush()

    # Count queries on the underlying session by patching execute.
    original_execute = db_session.execute
    call_count = {"n": 0}

    async def counting_execute(*args, **kwargs):
        call_count["n"] += 1
        return await original_execute(*args, **kwargs)

    db_session.execute = counting_execute  # type: ignore[method-assign]
    try:
        ready = await _find_ready_children(db_session, parent)
    finally:
        db_session.execute = original_execute  # type: ignore[method-assign]

    assert len(ready) == 8
    # Batched implementation: at most one "find dependent ids" + one
    # "load candidate runs" + one "load all dep statuses for the
    # candidates" = 3 queries; allow 4 for headroom but reject 8+ which
    # would indicate the per-candidate loop is back.
    assert call_count["n"] <= 4, (
        f"_find_ready_children issued {call_count['n']} DB round-trips "
        f"for 8 candidates — expected <=4 (batch query). N+1 regression."
    )
