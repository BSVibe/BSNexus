"""Tests for the manual deliverable re-verify endpoint (G7.3).

Decision-locks A1 promised the founder a one-tap re-verify on every
DeliverableCard. The route was specified in CLAUDE.md but missed in
G4 — DeliverableCard's "Re-verify" button currently 404s.

This endpoint is the *enqueue* path: stamp ``proof_state=verifying``,
record a fresh ProofAttempt(running), and publish ``deliverable_proof``
on the project's SSE stream. The deterministic worker that completes
the attempt lands in the quality-engineering phase; until then the
button gives the founder visible feedback (badge flips to
``verifying``) and a queue entry the worker will pick up.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from backend.src.core.domain import (
    DeliverableStatus,
    DeliverableType,
    ProofAttemptStatus,
    ProofState,
)
from backend.src.models import Deliverable, Project, ProofAttempt


async def _make_project(db_session, tenant_id: uuid.UUID, name: str = "Verify EP") -> Project:
    project = Project(tenant_id=tenant_id, name=name, description="")
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)
    return project


async def _make_deliverable(
    db_session,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    *,
    proof_state: ProofState = ProofState.verification_failed,
    status: DeliverableStatus = DeliverableStatus.shipped,
) -> Deliverable:
    deliverable = Deliverable(
        tenant_id=tenant_id,
        project_id=project_id,
        type=DeliverableType.code,
        title="Failing implementation",
        artifact_refs=["git:abc"],
        status=status,
        proof_state=proof_state,
    )
    db_session.add(deliverable)
    await db_session.commit()
    await db_session.refresh(deliverable)
    return deliverable


@pytest.mark.asyncio
async def test_verify_endpoint_returns_verifying_state(
    client, db_session, mock_tenant_id, seeded_tenant, mock_stream_manager
):
    project = await _make_project(db_session, mock_tenant_id)
    deliverable = await _make_deliverable(
        db_session, mock_tenant_id, project.id,
        proof_state=ProofState.verification_failed,
    )

    resp = await client.post(
        f"/api/v1/deliverables/{deliverable.id}/verify",
        headers={"Authorization": "Bearer fake"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == str(deliverable.id)
    # ProofBadge on the frontend renders the ``verifying`` state with a
    # spinner; the founder sees the click took effect immediately even
    # before the worker completes.
    assert body["proof_state"] == "verifying"
    assert body["status"] == "verifying"


@pytest.mark.asyncio
async def test_verify_endpoint_creates_proof_attempt_in_running_state(
    client, db_session, mock_tenant_id, seeded_tenant, mock_stream_manager
):
    project = await _make_project(db_session, mock_tenant_id)
    deliverable = await _make_deliverable(db_session, mock_tenant_id, project.id)

    await client.post(
        f"/api/v1/deliverables/{deliverable.id}/verify",
        headers={"Authorization": "Bearer fake"},
    )

    attempts = list(
        (
            await db_session.execute(
                select(ProofAttempt).where(ProofAttempt.deliverable_id == deliverable.id)
            )
        ).scalars()
    )
    assert len(attempts) == 1
    [attempt] = attempts
    assert attempt.status == ProofAttemptStatus.running
    # The worker that completes the attempt will overwrite verifier_type
    # with the matched policy; the enqueue marker keeps the trigger
    # provenance auditable.
    assert "manual_re_verify" in (attempt.inputs or {}).get("trigger", "")


@pytest.mark.asyncio
async def test_verify_endpoint_publishes_deliverable_proof_event(
    client, db_session, mock_tenant_id, seeded_tenant, mock_stream_manager
):
    project = await _make_project(db_session, mock_tenant_id)
    deliverable = await _make_deliverable(db_session, mock_tenant_id, project.id)

    await client.post(
        f"/api/v1/deliverables/{deliverable.id}/verify",
        headers={"Authorization": "Bearer fake"},
    )

    mock_stream_manager.publish_project_event.assert_called_once()
    call = mock_stream_manager.publish_project_event.call_args
    args = call.args
    assert args[0] == str(project.id)
    assert args[1] == "deliverable_proof"
    payload = args[2]
    assert payload["id"] == str(deliverable.id)
    assert payload["project_id"] == str(project.id)
    assert payload["proof_state"] == "verifying"


@pytest.mark.asyncio
async def test_verify_endpoint_404s_cross_tenant_deliverable(
    client, db_session, mock_tenant_id, seeded_tenant
):
    """Another tenant's deliverable returns 404 — verifier triggers must
    never cross tenant boundaries (the worker would otherwise burn one
    tenant's verifier capacity on another tenant's row)."""
    from backend.src.models import Tenant

    other_tenant_id = uuid.uuid4()
    db_session.add(
        Tenant(
            id=other_tenant_id,
            name="Other",
            slug=f"other-{other_tenant_id.hex[:8]}",
            owner_user_id="other-user",
        )
    )
    await db_session.commit()
    other_project = await _make_project(db_session, other_tenant_id, "OtherProj")
    other_deliverable = await _make_deliverable(db_session, other_tenant_id, other_project.id)

    resp = await client.post(
        f"/api/v1/deliverables/{other_deliverable.id}/verify",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_verify_endpoint_404s_unknown_deliverable(
    client, db_session, mock_tenant_id, seeded_tenant
):
    resp = await client.post(
        f"/api/v1/deliverables/{uuid.uuid4()}/verify",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 404
