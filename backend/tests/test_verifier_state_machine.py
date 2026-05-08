"""VerifierStateMachine — proof-state transitions on Deliverable rows."""

from __future__ import annotations

import uuid

import pytest
from unittest.mock import AsyncMock

from backend.src.core.verifier.protocol import (
    VerificationResult,
    VerifierProofState,
)
from backend.src.core.verifier.state_machine import (
    InvalidProofTransitionError,
    VerifierStateMachine,
    to_model_state,
)
from backend.src.models import (
    Deliverable,
    DeliverableStatus,
    DeliverableType,
    ProofState,
    Project,
)


async def _seed_deliverable(db_session, tenant_id: uuid.UUID, **overrides) -> Deliverable:
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
async def test_transition_missing_to_verifying(db_session, mock_tenant_id, seeded_tenant) -> None:
    sm = VerifierStateMachine()
    d = await _seed_deliverable(db_session, mock_tenant_id)
    assert d.proof_state == ProofState.verification_missing

    await sm.transition(d, ProofState.verifying, db_session=db_session)
    await db_session.commit()
    await db_session.refresh(d)

    assert d.proof_state == ProofState.verifying
    assert d.verified_at is None


@pytest.mark.asyncio
async def test_transition_to_verified_stamps_fields(db_session, mock_tenant_id, seeded_tenant) -> None:
    sm = VerifierStateMachine()
    d = await _seed_deliverable(db_session, mock_tenant_id, proof_state=ProofState.verifying)

    result = VerificationResult(
        proof_state=VerifierProofState.verified,
        exit_code=0,
        summary="12 passed in 0.4s",
        proof_refs=[{"label": "stdout", "type": "log", "href": "inline://stdout"}],
        risk_summary=None,
    )
    await sm.transition(d, ProofState.verified, result=result, db_session=db_session)
    await db_session.commit()
    await db_session.refresh(d)

    assert d.proof_state == ProofState.verified
    assert d.verification_exit_code == 0
    assert d.proof_summary == "12 passed in 0.4s"
    assert d.proof_refs == [{"label": "stdout", "type": "log", "href": "inline://stdout"}]
    assert d.verified_at is not None


@pytest.mark.asyncio
async def test_transition_to_verification_failed_records_exit_code(db_session, mock_tenant_id, seeded_tenant) -> None:
    sm = VerifierStateMachine()
    d = await _seed_deliverable(db_session, mock_tenant_id, proof_state=ProofState.verifying)

    result = VerificationResult(
        proof_state=VerifierProofState.verification_failed,
        exit_code=2,
        summary="exit=2 · pytest 1 failed",
        proof_refs=[{"label": "stderr", "type": "log", "href": "inline://stderr"}],
    )
    await sm.transition(d, ProofState.verification_failed, result=result, db_session=db_session)
    await db_session.commit()
    await db_session.refresh(d)

    assert d.proof_state == ProofState.verification_failed
    assert d.verification_exit_code == 2
    assert d.proof_summary == "exit=2 · pytest 1 failed"
    assert d.verified_at is None


@pytest.mark.asyncio
async def test_invalid_transition_raises(db_session, mock_tenant_id, seeded_tenant) -> None:
    sm = VerifierStateMachine()
    d = await _seed_deliverable(db_session, mock_tenant_id)
    # missing → verified is illegal (must go through verifying first)
    with pytest.raises(InvalidProofTransitionError):
        await sm.transition(d, ProofState.verified, db_session=db_session)


@pytest.mark.asyncio
async def test_terminal_verified_rejects_further_transitions(db_session, mock_tenant_id, seeded_tenant) -> None:
    sm = VerifierStateMachine()
    d = await _seed_deliverable(db_session, mock_tenant_id, proof_state=ProofState.verified)
    with pytest.raises(InvalidProofTransitionError):
        await sm.transition(d, ProofState.verifying, db_session=db_session)


@pytest.mark.asyncio
async def test_failed_can_re_enter_verifying_for_retry(db_session, mock_tenant_id, seeded_tenant) -> None:
    sm = VerifierStateMachine()
    d = await _seed_deliverable(db_session, mock_tenant_id, proof_state=ProofState.verification_failed)
    await sm.transition(d, ProofState.verifying, db_session=db_session)
    await db_session.commit()
    await db_session.refresh(d)
    assert d.proof_state == ProofState.verifying


@pytest.mark.asyncio
async def test_idempotent_transition_to_same_state_is_noop(db_session, mock_tenant_id, seeded_tenant) -> None:
    sm = VerifierStateMachine()
    d = await _seed_deliverable(db_session, mock_tenant_id, proof_state=ProofState.verifying)
    out = await sm.transition(d, ProofState.verifying, db_session=db_session)
    assert out is d
    assert d.proof_state == ProofState.verifying


@pytest.mark.asyncio
async def test_transition_publishes_sse_event_when_stream_manager_supplied(
    db_session, mock_tenant_id, seeded_tenant
) -> None:
    sm = VerifierStateMachine()
    d = await _seed_deliverable(db_session, mock_tenant_id)

    stream_manager = AsyncMock()
    await sm.transition(
        d,
        ProofState.verifying,
        db_session=db_session,
        stream_manager=stream_manager,
    )

    stream_manager.publish_project_event.assert_awaited_once()
    args, _ = stream_manager.publish_project_event.call_args
    assert args[0] == str(d.project_id)
    assert args[1] == "deliverable_proof"
    assert args[2]["from_state"] == "verification_missing"
    assert args[2]["to_state"] == "verifying"


def test_to_model_state_maps_protocol_enum() -> None:
    assert to_model_state(VerifierProofState.verified) == ProofState.verified
    assert to_model_state(VerifierProofState.not_applicable) == ProofState.not_applicable
