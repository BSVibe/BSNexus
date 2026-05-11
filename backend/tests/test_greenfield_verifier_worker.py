"""Tests for the deterministic VerifierWorker (G6.1).

G7.3 added the ``/api/v1/deliverables/{id}/verify`` route that stamps
``proof_state=verifying`` + a ``ProofAttempt(running)`` row, but the
consumer that actually runs the verifier was a TODO. G6.1 closes that
loop: the route now also publishes a `proof:queue` Redis Stream entry,
and ``VerifierWorker`` consumes it, calls
``run_proof_attempt()``, flips the deliverable state, and fans the
``deliverable_proof`` SSE event so open BSNexus tabs refresh.

The deterministic verifier lives in ``backend/src/core/proof.py``; the
worker is a thin consumer loop around it.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from backend.src.core.domain import (
    DeliverableStatus,
    DeliverableType,
    ProofState,
)
from backend.src.models import Deliverable, Project
from backend.src.models.project import WorkspaceType


def _seed_python_workspace(root: Path) -> None:
    """Set up a deterministic pytest-passing workspace at ``root``."""
    (root / "pyproject.toml").write_text("[project]\nname = 'qa-workspace'\nversion = '0.0.0'\n")
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests" / "__init__.py").write_text("")
    (root / "tests" / "test_smoke.py").write_text("def test_one_plus_one():\n    assert 1 + 1 == 2\n")


def _seed_python_failing_workspace(root: Path) -> None:
    """Same shape, but the verifier command fails (exit_code != 0)."""
    (root / "pyproject.toml").write_text("[project]\nname = 'qa-workspace'\nversion = '0.0.0'\n")
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests" / "__init__.py").write_text("")
    (root / "tests" / "test_smoke.py").write_text("def test_one_plus_one():\n    assert 1 + 1 == 3\n")


async def _seed_project_and_deliverable(
    db_session,
    tenant_id: uuid.UUID,
    workspace_dir: Path,
    *,
    proof_state: ProofState = ProofState.verifying,
) -> Deliverable:
    project = Project(
        tenant_id=tenant_id,
        name="QA worker test",
        workspace_type=WorkspaceType.local_import,
        workspace_dir=str(workspace_dir),
    )
    db_session.add(project)
    await db_session.flush()
    deliverable = Deliverable(
        tenant_id=tenant_id,
        project_id=project.id,
        type=DeliverableType.code,
        title="QA worker target",
        artifact_refs=[],
        status=DeliverableStatus.verifying,
        proof_state=proof_state,
    )
    db_session.add(deliverable)
    await db_session.commit()
    await db_session.refresh(deliverable)
    return deliverable


@pytest.mark.asyncio
async def test_verifier_worker_stamps_verified_on_passing_python_workspace(
    db_session, tmp_path, mock_tenant_id, seeded_tenant
):
    """The worker pulls a queued deliverable, runs the deterministic
    python_test policy, and flips proof_state → verified when the test
    suite passes."""
    from backend.src.workers.verifier import process_one

    _seed_python_workspace(tmp_path)
    deliverable = await _seed_project_and_deliverable(db_session, mock_tenant_id, tmp_path)

    await process_one(
        deliverable_id=deliverable.id,
        tenant_id=mock_tenant_id,
        session=db_session,
        publish_event=None,
    )

    # ``run_proof_attempt`` already commits + refreshes; reading in-place.
    refreshed = deliverable
    assert refreshed is not None
    assert refreshed.proof_state == ProofState.verified
    assert refreshed.status == DeliverableStatus.review_ready


@pytest.mark.asyncio
async def test_verifier_worker_stamps_verification_failed_when_tests_fail(
    db_session, tmp_path, mock_tenant_id, seeded_tenant
):
    """A failing pytest run leaves the deliverable in
    ``verification_failed`` with the latest ``ProofAttempt`` marked
    failed. The founder UI surfaces this as the rose "검증 실패"
    badge."""
    from backend.src.workers.verifier import process_one

    _seed_python_failing_workspace(tmp_path)
    deliverable = await _seed_project_and_deliverable(db_session, mock_tenant_id, tmp_path)

    await process_one(
        deliverable_id=deliverable.id,
        tenant_id=mock_tenant_id,
        session=db_session,
        publish_event=None,
    )

    # ``run_proof_attempt`` already commits + refreshes; reading in-place.
    refreshed = deliverable
    assert refreshed is not None
    assert refreshed.proof_state == ProofState.verification_failed


@pytest.mark.asyncio
async def test_verifier_worker_marks_human_review_when_no_policy_matches(
    db_session, tmp_path, mock_tenant_id, seeded_tenant
):
    """A workspace with no pyproject/package.json gets the
    ``human_review_required`` proof_state instead of a fake-verified.
    G6 spec: ``fake_verified == 0`` is the gate."""
    from backend.src.workers.verifier import process_one

    # No pyproject, no package.json → no policy.
    (tmp_path / "README.md").write_text("# nothing to verify\n")
    deliverable = await _seed_project_and_deliverable(db_session, mock_tenant_id, tmp_path)

    await process_one(
        deliverable_id=deliverable.id,
        tenant_id=mock_tenant_id,
        session=db_session,
        publish_event=None,
    )

    # ``run_proof_attempt`` already commits + refreshes; reading in-place.
    refreshed = deliverable
    assert refreshed is not None
    assert refreshed.proof_state == ProofState.human_review_required


@pytest.mark.asyncio
async def test_verifier_worker_publishes_deliverable_proof_event(db_session, tmp_path, mock_tenant_id, seeded_tenant):
    """The worker fans a ``deliverable_proof`` event onto the project
    stream so other open tabs refresh without a manual reload (mirrors
    the SSE behaviour from the /verify route handler)."""
    from backend.src.workers.verifier import process_one

    _seed_python_workspace(tmp_path)
    deliverable = await _seed_project_and_deliverable(db_session, mock_tenant_id, tmp_path)

    events: list[tuple[str, str, dict]] = []

    async def publish_event(project_id: str, event: str, payload: dict) -> None:
        events.append((project_id, event, payload))

    await process_one(
        deliverable_id=deliverable.id,
        tenant_id=mock_tenant_id,
        session=db_session,
        publish_event=publish_event,
    )

    assert len(events) == 1
    project_id, event_name, payload = events[0]
    assert project_id == str(deliverable.project_id)
    assert event_name == "deliverable_proof"
    assert payload["id"] == str(deliverable.id)
    assert payload["proof_state"] == ProofState.verified.value


@pytest.mark.asyncio
async def test_verifier_worker_skips_cross_tenant_deliverable(db_session, tmp_path, mock_tenant_id, seeded_tenant):
    """A deliverable that belongs to another tenant is not processed
    even if the consumer somehow received its message — tenant scope
    must hold at the worker boundary."""
    from backend.src.models import Tenant
    from backend.src.workers.verifier import process_one

    other_tenant_id = uuid.uuid4()
    db_session.add(
        Tenant(
            id=other_tenant_id,
            name="Other",
            slug=f"other-{other_tenant_id.hex[:8]}",
            owner_user_id="other-user",
        )
    )
    await db_session.flush()

    _seed_python_workspace(tmp_path)
    deliverable = await _seed_project_and_deliverable(db_session, other_tenant_id, tmp_path)

    with pytest.raises(LookupError):
        await process_one(
            deliverable_id=deliverable.id,
            tenant_id=mock_tenant_id,  # different from the deliverable's tenant
            session=db_session,
            publish_event=None,
        )


@pytest.mark.asyncio
async def test_verify_route_enqueues_proof_queue_message(
    client, db_session, tmp_path, mock_tenant_id, seeded_tenant, mock_stream_manager
):
    """G6.1 — the /verify route, in addition to its existing
    ``deliverable_proof`` SSE fan-out and ``ProofAttempt(running)``
    insert, must enqueue a ``proof:queue`` message so the worker picks
    it up. Without this, the SSE event fires but the deliverable
    sits in ``verifying`` forever (the bug A1 was trying to close)."""
    deliverable = await _seed_project_and_deliverable(db_session, mock_tenant_id, tmp_path)

    resp = await client.post(
        f"/api/v1/deliverables/{deliverable.id}/verify",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200, resp.text

    # ``publish`` is the generic stream-write primitive on
    # RedisStreamManager. We expect at least one call against the
    # ``proof:queue`` stream — the worker target.
    publish_calls = mock_stream_manager.publish.await_args_list
    proof_queue_calls = [call for call in publish_calls if call.args and call.args[0] == "proof:queue"]
    assert len(proof_queue_calls) == 1, f"expected 1 proof:queue enqueue, got {publish_calls}"
    payload = proof_queue_calls[0].args[1]
    assert payload["deliverable_id"] == str(deliverable.id)
    assert payload["tenant_id"] == str(mock_tenant_id)
