from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from backend.src.core.domain import (
    DeliverableStatus,
    DeliverableType,
    ProofAttemptStatus,
    ProofState,
    RequestStatus,
)
from backend.src.models import Decision, Deliverable, Project, ProofAttempt, Request


async def _make_project(db_session, tenant_id: uuid.UUID, name: str = "Greenfield G5") -> Project:
    project = Project(tenant_id=tenant_id, name=name, description="")
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)
    return project


async def _make_request(
    db_session,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    *,
    intent: str,
    status: RequestStatus = RequestStatus.open,
) -> Request:
    request = Request(
        tenant_id=tenant_id,
        project_id=project_id,
        intent=intent,
        status=status,
    )
    db_session.add(request)
    await db_session.commit()
    await db_session.refresh(request)
    return request


@pytest.mark.asyncio
async def test_post_deliverable_persists_work_output_without_dispatching_verifier(
    client,
    db_session,
    mock_tenant_id,
):
    project = await _make_project(db_session, mock_tenant_id)
    request = await _make_request(
        db_session,
        mock_tenant_id,
        project.id,
        intent="Ship proof-aware deliverables",
    )

    resp = await client.post(
        "/api/v1/deliverables",
        json={
            "project_id": str(project.id),
            "request_id": str(request.id),
            "type": "code",
            "title": "Proof-aware deliverable API",
            "summary": "Stores work output without claiming proof.",
            "artifact_refs": ["git:abc123"],
            "risk_summary": "Verifier has not run yet.",
        },
        headers={"Authorization": "Bearer fake"},
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["project_id"] == str(project.id)
    assert body["request_id"] == str(request.id)
    assert body["status"] == "draft"
    assert body["proof_state"] == "verification_missing"
    assert body["proof_status"] == {
        "state": "verification_missing",
        "policy_id": None,
        "latest_attempt_id": None,
        "latest_attempt_status": None,
        "latest_attempt_summary": None,
        "latest_attempt_completed_at": None,
    }

    deliverable = await db_session.get(Deliverable, uuid.UUID(body["id"]))
    assert deliverable is not None
    assert deliverable.proof_state == ProofState.verification_missing
    attempts = (
        (await db_session.execute(select(ProofAttempt).where(ProofAttempt.deliverable_id == deliverable.id)))
        .scalars()
        .all()
    )
    assert attempts == []


@pytest.mark.asyncio
async def test_deliverable_list_surfaces_latest_proof_status(client, db_session, mock_tenant_id):
    project = await _make_project(db_session, mock_tenant_id)
    deliverable = Deliverable(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        type=DeliverableType.code,
        title="Implementation",
        artifact_refs=["git:abc123"],
        status=DeliverableStatus.review_ready,
        proof_state=ProofState.verified,
    )
    db_session.add(deliverable)
    await db_session.flush()
    attempt = ProofAttempt(
        deliverable_id=deliverable.id,
        verifier_type="python_test",
        inputs={"command": ["python", "-m", "pytest"]},
        status=ProofAttemptStatus.verified,
        exit_code=0,
        proof_summary="pytest passed",
        proof_refs=[{"kind": "verifier_command"}],
    )
    db_session.add(attempt)
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/deliverables?project_id={project.id}",
        headers={"Authorization": "Bearer fake"},
    )

    assert resp.status_code == 200, resp.text
    [card] = resp.json()
    assert card["proof_status"]["state"] == "verified"
    assert card["proof_status"]["latest_attempt_id"] == str(attempt.id)
    assert card["proof_status"]["latest_attempt_status"] == "verified"
    assert card["proof_status"]["latest_attempt_summary"] == "pytest passed"


@pytest.mark.asyncio
async def test_brief_aggregates_mobile_friendly_sections_and_never_ships_missing_proof(
    client,
    db_session,
    mock_tenant_id,
):
    project = await _make_project(db_session, mock_tenant_id)
    next_request = await _make_request(
        db_session,
        mock_tenant_id,
        project.id,
        intent="Next request",
        status=RequestStatus.open,
    )
    running_request = await _make_request(
        db_session,
        mock_tenant_id,
        project.id,
        intent="Running request",
        status=RequestStatus.running,
    )
    blocked_request = await _make_request(
        db_session,
        mock_tenant_id,
        project.id,
        intent="Blocked request",
        status=RequestStatus.blocked,
    )
    decision = Decision(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        request_id=blocked_request.id,
        question="Ship with current test evidence?",
        options=["ship", "hold"],
        blocking=True,
    )
    verified = Deliverable(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        request_id=running_request.id,
        type=DeliverableType.code,
        title="Verified implementation",
        artifact_refs=["git:verified"],
        status=DeliverableStatus.shipped,
        proof_state=ProofState.verified,
    )
    missing_proof = Deliverable(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        request_id=next_request.id,
        type=DeliverableType.code,
        title="Missing proof implementation",
        artifact_refs=["git:missing"],
        status=DeliverableStatus.shipped,
        proof_state=ProofState.verification_missing,
    )
    db_session.add_all([decision, verified, missing_proof])
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/brief?project_id={project.id}",
        headers={"Authorization": "Bearer fake"},
    )

    assert resp.status_code == 200, resp.text
    brief = resp.json()
    assert brief["scope"] == "project"
    assert brief["project_id"] == str(project.id)
    assert set(brief["sections"]) == {"shipped", "needs_decision", "blocked", "running", "next"}

    shipped_ids = {card["id"] for card in brief["sections"]["shipped"]}
    assert str(verified.id) in shipped_ids
    assert str(missing_proof.id) not in shipped_ids
    assert brief["sections"]["shipped"][0]["proof_state"] == "verified"
    assert brief["sections"]["shipped"][0]["label"] == "shipped"

    blocked_cards = brief["sections"]["blocked"]
    assert any(card["kind"] == "request" and card["id"] == str(blocked_request.id) for card in blocked_cards)
    assert any(
        card["kind"] == "deliverable"
        and card["id"] == str(missing_proof.id)
        and card["proof_state"] == "verification_missing"
        and card["label"] == "blocked"
        for card in blocked_cards
    )
    assert brief["sections"]["needs_decision"][0]["id"] == str(decision.id)
    assert brief["sections"]["running"][0]["id"] == str(running_request.id)
    assert brief["sections"]["next"][0]["id"] == str(next_request.id)
