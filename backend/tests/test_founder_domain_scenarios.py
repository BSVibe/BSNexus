"""S4 — Founder-metaphor domain scenario tests (Audit §6 BSNexus-특화).

The founder-metaphor migration retired Agent/Task/Phase tables and
introduced Request/ExecutionRun/Deliverable/Decision/CompositionSnapshot
as the user-facing domain. Audit §6 calls out the gap: existing tests
are per-router contract-only; **scenarios that span multiple domain
objects** are missing.

These tests pin domain-level scenarios:

  * Decisions resolve flow forwards the SSO Bearer to BSage
    ``record_decision`` (per-call ``auth_token``).
  * Decisions resolve continues even if the BSage record_decision call
    fails (Noop fallback) — the user-visible 200 is preserved.
  * Project deletion cascades through Request/Deliverable/Decision/
    CompositionSnapshot/ExecutionRun (the founder-metaphor cascade).
  * ExecutionRunHistory rows persist after run.status changes via the
    state machine — pin the audit trail.
  * A request with multiple completed runs feeds ``_load_prior_iterations``
    deterministically (no ordering bug).
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select

from backend.src.core.run_orchestrator import _load_prior_iterations_for_compose
from backend.src.core.state_machine import RunStateMachine
from backend.src.models import (
    CompositionSnapshot,
    CompositionSource,
    Decision,
    Deliverable,
    DeliverableStatus,
    DeliverableType,
    ExecutionRun,
    ExecutionRunHistory,
    Project,
    Request,
    RequestStatus,
    RunStatus,
)


async def _make_project(client, name: str = "P") -> str:
    resp = await client.post(
        "/api/v1/projects",
        json={"name": name},
        headers={"Authorization": "Bearer founder-jwt-eyJ"},
    )
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_decision_resolve_forwards_bearer_to_bsage(client, db_session, mock_tenant_id) -> None:
    """When the founder resolves a decision, the SSO Bearer JWT must
    flow through to BSage's ``record_decision`` — pin the per-call
    ``auth_token`` plumbing that drives same-account audit attribution."""
    pid = await _make_project(client)

    decision = Decision(
        tenant_id=mock_tenant_id,
        project_id=uuid.UUID(pid),
        question="Should we use Postgres?",
        options=["yes", "no"],
        blocking=True,
    )
    db_session.add(decision)
    await db_session.commit()
    await db_session.refresh(decision)

    captured: dict = {}

    class _SpyKnowledge:
        async def record_decision(self, **kwargs):
            captured.update(kwargs)
            return None

        async def search(self, *a, **k):  # pragma: no cover - unused here
            return []

        async def fetch(self, *a, **k):  # pragma: no cover - unused here
            return None

        async def backlinks(self, *a, **k):  # pragma: no cover - unused here
            return []

        async def index(self, *a, **k):  # pragma: no cover - unused here
            return None

    spy = _SpyKnowledge()

    with patch("backend.src.api.decisions.resolve_knowledge_client", return_value=spy):
        resp = await client.post(
            f"/api/v1/decisions/{decision.id}/resolve",
            json={"resolution": "yes", "resolved_by": "founder"},
            headers={"Authorization": "Bearer founder-jwt-eyJ"},
        )

    assert resp.status_code == 200
    # The resolve handler must have forwarded the founder's bearer token
    # so BSage records the decision under the founder's identity.
    assert captured.get("auth_token") == "founder-jwt-eyJ"
    assert captured["decision"] == "yes"
    assert "blocking" in captured["tags"]


@pytest.mark.asyncio
async def test_decision_resolve_succeeds_even_if_bsage_record_fails(client, db_session, mock_tenant_id) -> None:
    """BSage ``record_decision`` returning None (transient failure) must
    NOT prevent the founder from resolving the decision. The user-facing
    response is 200 either way — pin degradable contract."""
    pid = await _make_project(client)
    decision = Decision(
        tenant_id=mock_tenant_id,
        project_id=uuid.UUID(pid),
        question="Pick stack",
        options=["a", "b"],
        blocking=False,
    )
    db_session.add(decision)
    await db_session.commit()
    await db_session.refresh(decision)

    class _FailingKnowledge:
        async def record_decision(self, **kwargs):
            return None  # transient failure surfaced as None

        async def search(self, *a, **k):
            return []

        async def fetch(self, *a, **k):
            return None

        async def backlinks(self, *a, **k):
            return []

        async def index(self, *a, **k):
            return None

    with patch(
        "backend.src.api.decisions.resolve_knowledge_client",
        return_value=_FailingKnowledge(),
    ):
        resp = await client.post(
            f"/api/v1/decisions/{decision.id}/resolve",
            json={"resolution": "a"},
            headers={"Authorization": "Bearer fake"},
        )

    assert resp.status_code == 200
    assert resp.json()["resolved_at"] is not None


@pytest.mark.asyncio
async def test_project_delete_cascades_to_founder_metaphor_rows(client, db_session, mock_tenant_id) -> None:
    """The founder-metaphor cascade: deleting a Project must cascade to
    Request, Deliverable, Decision, CompositionSnapshot, ExecutionRun.
    Audit §6 cites this gap because the cascade was rewired during the
    metaphor migration and we don't have an explicit assertion."""
    pid = await _make_project(client)
    pid_uuid = uuid.UUID(pid)

    request = Request(
        tenant_id=mock_tenant_id,
        project_id=pid_uuid,
        intent_summary="Plan",
        status=RequestStatus.open,
    )
    db_session.add(request)
    await db_session.flush()

    db_session.add_all(
        [
            Deliverable(
                tenant_id=mock_tenant_id,
                project_id=pid_uuid,
                type=DeliverableType.doc,
                title="d",
                status=DeliverableStatus.draft,
            ),
            Decision(
                tenant_id=mock_tenant_id,
                project_id=pid_uuid,
                question="?",
                options=[],
                blocking=False,
            ),
        ]
    )
    run = ExecutionRun(
        tenant_id=mock_tenant_id,
        project_id=pid_uuid,
        request_id=request.id,
        status=RunStatus.pending,
    )
    db_session.add(run)
    await db_session.flush()
    snapshot = CompositionSnapshot(
        tenant_id=mock_tenant_id,
        request_id=request.id,
        execution_run_id=run.id,
        source=CompositionSource.local,
        system_prompt_ref={"inline": "x"},
        tools_allowed=["read"],
        context_doc_refs=[],
        persona_label="builder",
    )
    db_session.add(snapshot)
    await db_session.commit()

    # Delete the project via the API.
    resp = await client.delete(f"/api/v1/projects/{pid}", headers={"Authorization": "Bearer fake"})
    assert resp.status_code in (200, 204)

    # Every founder-metaphor row tied to the project is gone.
    for model in (Request, Deliverable, Decision, ExecutionRun, CompositionSnapshot):
        rows = (await db_session.execute(select(model))).scalars().all()
        # Only assert the rows linked to *this* project are gone — the
        # CompositionSnapshot doesn't have a direct project_id, but it
        # cascades via execution_run_id which cascades from project.
        if model is CompositionSnapshot:
            related = [r for r in rows if r.execution_run_id == run.id]
            assert related == [], "CompositionSnapshot leaked after project delete"
        else:
            related = [r for r in rows if getattr(r, "project_id", None) == pid_uuid]
            assert related == [], f"{model.__name__} leaked after project delete"


@pytest.mark.asyncio
async def test_run_state_machine_writes_history_for_every_transition(db_session, mock_tenant_id, seeded_tenant):
    """Pinning the audit trail: each ``RunStateMachine.transition`` call
    must add an ExecutionRunHistory row even without a stream manager.
    The Inside panel's run history view depends on this."""
    project = Project(tenant_id=mock_tenant_id, name="Audit", description="")
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
        status=RunStatus.pending,
    )
    db_session.add(run)
    await db_session.commit()

    sm = RunStateMachine()
    await sm.transition(run, RunStatus.running, actor="orchestrator", db_session=db_session)
    await sm.transition(run, RunStatus.blocked, actor="audit", reason="rule_x", db_session=db_session)
    await sm.transition(run, RunStatus.pending, actor="watchdog", db_session=db_session)
    await sm.transition(run, RunStatus.running, actor="orchestrator", db_session=db_session)
    await sm.transition(run, RunStatus.done, actor="orchestrator", db_session=db_session)
    await db_session.commit()

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
    assert len(history) == 5
    transitions = [(h.from_status, h.to_status, h.actor) for h in history]
    assert transitions == [
        (RunStatus.pending, RunStatus.running, "orchestrator"),
        (RunStatus.running, RunStatus.blocked, "audit"),
        (RunStatus.blocked, RunStatus.pending, "watchdog"),
        (RunStatus.pending, RunStatus.running, "orchestrator"),
        (RunStatus.running, RunStatus.done, "orchestrator"),
    ]


@pytest.mark.asyncio
async def test_load_prior_iterations_returns_completed_runs_in_order(db_session, mock_tenant_id, seeded_tenant):
    """``_load_prior_iterations_for_compose`` feeds the worker prompt with
    a chronological list of prior completed runs. Pin that:
      * Only ``done`` runs are returned.
      * Ordering is by ``created_at`` ascending (re-creating the original
        sequence the founder saw).
      * The current run is excluded.
      * ``files_written`` is extracted defensively from
        ``output_ref.files``.
    """
    project = Project(tenant_id=mock_tenant_id, name="Iter", description="")
    db_session.add(project)
    await db_session.flush()

    request = Request(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        intent_summary="multi-pass",
        status=RequestStatus.open,
    )
    db_session.add(request)
    await db_session.flush()

    # Create 4 runs: 2 done, 1 blocked, plus the current one.
    runs = [
        ExecutionRun(
            tenant_id=mock_tenant_id,
            project_id=project.id,
            request_id=request.id,
            status=RunStatus.done,
            directive="Step 1: scaffold",
            output_ref={"founder_summary": "scaffolded", "files": [{"path": "src/a.py"}]},
        ),
        ExecutionRun(
            tenant_id=mock_tenant_id,
            project_id=project.id,
            request_id=request.id,
            status=RunStatus.done,
            directive="Step 2: tests",
            output_ref={"founder_summary": "tested", "files": [{"path": "tests/a.py"}]},
        ),
        ExecutionRun(
            tenant_id=mock_tenant_id,
            project_id=project.id,
            request_id=request.id,
            status=RunStatus.blocked,
            directive="Step 3 (failed)",
            output_ref={"founder_summary": "blocked"},
        ),
    ]
    for r in runs:
        db_session.add(r)
        await db_session.flush()

    current = ExecutionRun(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        request_id=request.id,
        status=RunStatus.running,
        directive="Step 4: in-flight",
    )
    db_session.add(current)
    await db_session.commit()

    iterations = await _load_prior_iterations_for_compose(db_session, current)
    assert len(iterations) == 2  # only done + not the current
    assert iterations[0]["name"].startswith("Step 1")
    assert iterations[1]["name"].startswith("Step 2")
    assert iterations[0]["files_written"] == ["src/a.py"]
    assert iterations[1]["files_written"] == ["tests/a.py"]


@pytest.mark.asyncio
async def test_decision_blocking_status_round_trips_through_api(client, db_session, mock_tenant_id):
    """``Decision.blocking`` is the ordering primitive for the
    Decisions inbox. Pin that the API response surfaces the field
    verbatim and the resolve handler doesn't accidentally clear it."""
    pid = await _make_project(client)
    decision = Decision(
        tenant_id=mock_tenant_id,
        project_id=uuid.UUID(pid),
        question="block me",
        options=["a", "b"],
        blocking=True,
    )
    db_session.add(decision)
    await db_session.commit()
    await db_session.refresh(decision)

    # List → blocking surfaces.
    listed = (
        await client.get(
            f"/api/v1/projects/{pid}/decisions",
            headers={"Authorization": "Bearer fake"},
        )
    ).json()
    assert listed[0]["blocking"] is True

    # Resolve → blocking still True after resolution.
    resolved = (
        await client.post(
            f"/api/v1/decisions/{decision.id}/resolve",
            json={"resolution": "a"},
            headers={"Authorization": "Bearer fake"},
        )
    ).json()
    assert resolved["blocking"] is True


@pytest.mark.asyncio
async def test_request_status_transitions_independent_of_run_status(client, db_session, mock_tenant_id):
    """Pin the founder-metaphor invariant: a Request's status is
    independent of its child ExecutionRun statuses. The replanner can
    keep emitting new runs (pending) while the parent Request stays
    'open'. Status is closed only when the founder marks it done."""
    pid = await _make_project(client)
    request = Request(
        tenant_id=mock_tenant_id,
        project_id=uuid.UUID(pid),
        intent_summary="long-running",
        status=RequestStatus.open,
    )
    db_session.add(request)
    await db_session.flush()

    # Create 3 runs in mixed states.
    db_session.add_all(
        [
            ExecutionRun(
                tenant_id=mock_tenant_id,
                project_id=uuid.UUID(pid),
                request_id=request.id,
                status=RunStatus.done,
            ),
            ExecutionRun(
                tenant_id=mock_tenant_id,
                project_id=uuid.UUID(pid),
                request_id=request.id,
                status=RunStatus.blocked,
            ),
            ExecutionRun(
                tenant_id=mock_tenant_id,
                project_id=uuid.UUID(pid),
                request_id=request.id,
                status=RunStatus.pending,
            ),
        ]
    )
    await db_session.commit()

    # The Request itself remains "open".
    rows = (
        await client.get(
            f"/api/v1/projects/{pid}/requests",
            headers={"Authorization": "Bearer fake"},
        )
    ).json()
    assert any(r["status"] == "open" for r in rows)
