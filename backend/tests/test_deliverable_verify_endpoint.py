"""POST /api/v1/deliverables/{id}/verify — manual proof re-enqueue."""

from __future__ import annotations

import uuid

import pytest

from backend.src.core.verifier.enqueue import VERIFICATION_QUEUE_STREAM
from backend.src.models import (
    Deliverable,
    DeliverableStatus,
    DeliverableType,
    Project,
)


async def _seed(db_session, tenant_id, **overrides) -> Deliverable:
    project = Project(tenant_id=tenant_id, name="P", description="")
    db_session.add(project)
    await db_session.flush()
    d = Deliverable(
        tenant_id=tenant_id,
        project_id=project.id,
        type=DeliverableType.code,
        title="t",
        status=DeliverableStatus.delivered,
        **overrides,
    )
    db_session.add(d)
    await db_session.commit()
    await db_session.refresh(d)
    return d


@pytest.mark.asyncio
async def test_verify_endpoint_enqueues_when_verifier_type_set(client, db_session, mock_tenant_id, mock_stream_manager) -> None:
    deliverable = await _seed(
        db_session,
        mock_tenant_id,
        verifier_type="software_test",
        verifier_inputs={"command": ["pytest"], "timeout_s": 60},
    )

    resp = await client.post(
        f"/api/v1/deliverables/{deliverable.id}/verify",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 202, resp.text

    body = resp.json()
    assert body["id"] == str(deliverable.id)
    assert body["verifier_type"] == "software_test"
    assert body["proof_state"] == "verification_missing"

    mock_stream_manager.publish.assert_awaited_once()
    args, _ = mock_stream_manager.publish.call_args
    assert args[0] == VERIFICATION_QUEUE_STREAM
    payload = args[1]
    assert payload["deliverable_id"] == str(deliverable.id)
    assert payload["tenant_id"] == str(mock_tenant_id)
    assert payload["verifier_type"] == "software_test"
    assert payload["inputs"] == {"command": ["pytest"], "timeout_s": 60}


@pytest.mark.asyncio
async def test_verify_endpoint_422_without_verifier_type(client, db_session, mock_tenant_id) -> None:
    deliverable = await _seed(db_session, mock_tenant_id)  # no verifier_type

    resp = await client.post(
        f"/api/v1/deliverables/{deliverable.id}/verify",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 422
    assert "verifier_type" in resp.text


@pytest.mark.asyncio
async def test_verify_endpoint_422_for_unsupported_verifier_type(client, db_session, mock_tenant_id) -> None:
    deliverable = await _seed(
        db_session,
        mock_tenant_id,
        verifier_type="design_screenshot",  # not yet in VerifierType enum
    )

    resp = await client.post(
        f"/api/v1/deliverables/{deliverable.id}/verify",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 422
    assert "Unsupported verifier_type" in resp.text


@pytest.mark.asyncio
async def test_verify_endpoint_404_for_foreign_tenant(client, db_session) -> None:
    other_tid = uuid.uuid4()
    from backend.src.models import Tenant

    db_session.add(
        Tenant(
            id=other_tid,
            name="Other",
            slug=f"o-{uuid.uuid4().hex[:8]}",
            owner_user_id="x",
        )
    )
    await db_session.commit()
    foreign = await _seed(
        db_session,
        other_tid,
        verifier_type="software_test",
        verifier_inputs={"command": ["true"]},
    )

    resp = await client.post(
        f"/api/v1/deliverables/{foreign.id}/verify",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_verify_endpoint_503_when_stream_manager_missing(test_app, client, db_session, mock_tenant_id) -> None:
    """Drop the stream_manager attribute the client fixture pinned and
    ensure the endpoint surfaces 503 instead of crashing."""
    deliverable = await _seed(
        db_session,
        mock_tenant_id,
        verifier_type="software_test",
        verifier_inputs={"command": ["true"]},
    )

    delattr(test_app.state, "stream_manager")
    try:
        resp = await client.post(
            f"/api/v1/deliverables/{deliverable.id}/verify",
            headers={"Authorization": "Bearer fake"},
        )
    finally:
        # Tests later in this file rely on the fixture's stream_manager.
        # Replace with a no-op so the fixture teardown's hasattr check is safe.
        from unittest.mock import AsyncMock as _AsyncMock

        test_app.state.stream_manager = _AsyncMock()

    assert resp.status_code == 503
