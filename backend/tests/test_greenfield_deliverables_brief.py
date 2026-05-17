from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from backend.src.core.domain import (
    DeliverableStatus,
    DeliverableType,
    ProofAspectStatus,
    ProofAspectType,
    ProofState,
    RequestStatus,
)
from backend.src.models import Decision, Deliverable, Project, Request, VerificationAspect


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
        "aspects": [],
        "latest_test_status": None,
        "latest_test_summary": None,
        "latest_test_completed_at": None,
    }

    deliverable = await db_session.get(Deliverable, uuid.UUID(body["id"]))
    assert deliverable is not None
    assert deliverable.proof_state == ProofState.verification_missing
    aspects = (
        (
            await db_session.execute(
                select(VerificationAspect).where(VerificationAspect.deliverable_id == deliverable.id)
            )
        )
        .scalars()
        .all()
    )
    assert aspects == []


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
    aspect = VerificationAspect(
        deliverable_id=deliverable.id,
        aspect_type=ProofAspectType.code_test,
        inputs={"commands": [["python", "-m", "pytest"]]},
        status=ProofAspectStatus.passed,
        exit_code=0,
        result_summary="pytest passed",
    )
    db_session.add(aspect)
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/deliverables?project_id={project.id}",
        headers={"Authorization": "Bearer fake"},
    )

    assert resp.status_code == 200, resp.text
    [card] = resp.json()
    assert card["proof_status"]["state"] == "verified"
    aspects = card["proof_status"]["aspects"]
    assert len(aspects) == 1
    assert aspects[0]["aspect_type"] == "code_test"
    assert aspects[0]["status"] == "passed"
    assert card["proof_status"]["latest_test_status"] == "passed"
    assert card["proof_status"]["latest_test_summary"] == "pytest passed"


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
        # Realistic post-verification state: run_verification stamps a
        # passing deliverable ``review_ready`` — nothing advances it to
        # ``shipped``. The brief "shipped" section keys on proof_state.
        status=DeliverableStatus.review_ready,
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

    # G7.1 — typed BriefDeliverableCard for shipped (no ``kind``/``label`` on
    # the homogeneous list, frontend renders these as DeliverableCard).
    shipped = brief["sections"]["shipped"]
    shipped_ids = {card["id"] for card in shipped}
    assert str(verified.id) in shipped_ids
    assert str(missing_proof.id) not in shipped_ids
    [shipped_card] = shipped
    assert shipped_card["proof_state"] == "verified"
    assert shipped_card["title"] == verified.title
    assert shipped_card["type"] == "code"
    assert "kind" not in shipped_card
    assert "label" not in shipped_card
    assert "request_id" in shipped_card
    assert "proof_summary" in shipped_card
    assert "verifier_type" in shipped_card
    assert "verified_at" in shipped_card
    assert "created_at" in shipped_card

    # G7.1 — blocked is a Pydantic discriminated union: blocked Requests
    # (kind=request) live alongside deliverables whose proof failed/missing
    # (kind=deliverable). Both surface to the founder; static type
    # narrowing happens on the frontend via the ``kind`` discriminator.
    blocked_cards = brief["sections"]["blocked"]
    blocked_request_card = next(
        card for card in blocked_cards if card["kind"] == "request" and card["id"] == str(blocked_request.id)
    )
    assert blocked_request_card["intent"] == "Blocked request"
    assert blocked_request_card["status"] == "blocked"
    assert "title" not in blocked_request_card  # renamed to ``intent`` for greenfield Request

    blocked_deliverable_card = next(
        card for card in blocked_cards if card["kind"] == "deliverable" and card["id"] == str(missing_proof.id)
    )
    assert blocked_deliverable_card["proof_state"] == "verification_missing"
    assert blocked_deliverable_card["title"] == missing_proof.title
    assert "label" not in blocked_deliverable_card  # ``label`` collapsed into ``kind`` discriminator

    # G7.1 — typed BriefDecisionCard for needs_decision; uses ``question``
    # (matches backend Decision.question column) not the legacy ``title``.
    [decision_card] = brief["sections"]["needs_decision"]
    assert decision_card["id"] == str(decision.id)
    assert decision_card["question"] == "Ship with current test evidence?"
    assert decision_card["blocking"] is True
    assert "title" not in decision_card

    # G7.1 — running is BriefRequestCard (Request rows directly, not Run).
    [running_card] = brief["sections"]["running"]
    assert running_card["id"] == str(running_request.id)
    assert running_card["intent"] == "Running request"
    assert running_card["status"] == "running"
    assert "kind" not in running_card  # homogeneous list

    # G7.1 — ``next`` is reserved for AI-recommended directions
    # (BriefNextHint shape: summary + request_id). We do not have an
    # AI-recommendation source yet, so the section is empty. The
    # legacy "stuff open requests into next" behavior was a hack.
    assert brief["sections"]["next"] == []
    # ``next_request`` exists but should NOT be in next; it's reachable
    # via /api/v1/requests?status=open.
    assert next_request is not None
