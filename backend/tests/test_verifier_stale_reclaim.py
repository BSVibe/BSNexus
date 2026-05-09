"""PR9 — auto-reclaim stale ``verification_missing`` deliverables.

Defense-in-depth for the verifier chain: if a deliverable has a
``verifier_type`` stamped (so the LLM did emit a fenced block) but
sits at ``proof_state=verification_missing`` past a threshold, it's
likely a dropped enqueue or worker-crash mid-process — not a
"no-block" deliberate skip. Re-enqueue it.

Cap retries at 3 via ``verifier_inputs.retry_count`` so we don't loop
forever on genuine verifier failures (those should land at
``verification_failed`` after one real run; if the worker keeps
returning early without transitioning the state, that's a separate
bug).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from backend.src.core.verifier.enqueue import VERIFICATION_QUEUE_STREAM
from backend.src.core.verifier.stale_reclaim import reclaim_stale_verifications
from backend.src.models import Deliverable, DeliverableStatus, DeliverableType, Project, ProofState


async def _seed_deliverable(
    db_session,
    tenant_id,
    *,
    proof_state: ProofState,
    verifier_type: str | None = "software_test",
    age_seconds: int = 120,
    retry_count: int = 0,
) -> Deliverable:
    project = Project(tenant_id=tenant_id, name=f"P-{uuid.uuid4().hex[:6]}", description="")
    db_session.add(project)
    await db_session.flush()

    inputs: dict | None = None
    if verifier_type is not None:
        inputs = {"command": ["true"], "cwd": ".", "timeout_s": 10}
        if retry_count > 0:
            inputs["retry_count"] = retry_count

    d = Deliverable(
        tenant_id=tenant_id,
        project_id=project.id,
        title="t",
        type=DeliverableType.code,
        status=DeliverableStatus.delivered,
        proof_state=proof_state,
        verifier_type=verifier_type,
        verifier_inputs=inputs,
    )
    db_session.add(d)
    await db_session.commit()
    # Override created_at to simulate age (server_default fires on
    # initial insert; we update post-commit to backdate).
    d.created_at = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    await db_session.commit()
    await db_session.refresh(d)
    return d


@pytest.mark.asyncio
async def test_reclaim_reenqueues_stale_verification_missing_with_verifier_type(
    db_session, mock_tenant_id, seeded_tenant
) -> None:
    d = await _seed_deliverable(
        db_session, mock_tenant_id, proof_state=ProofState.verification_missing, age_seconds=120
    )
    stream_manager = AsyncMock()
    stream_manager.publish = AsyncMock(return_value="0-0")

    n = await reclaim_stale_verifications(db_session, stream_manager, stale_after_s=60, max_retries=3)
    await db_session.commit()

    assert n == 1
    stream_manager.publish.assert_awaited_once()
    args, _ = stream_manager.publish.call_args
    assert args[0] == VERIFICATION_QUEUE_STREAM
    assert args[1]["deliverable_id"] == str(d.id)

    # retry_count incremented in verifier_inputs.
    refreshed = (await db_session.execute(select(Deliverable).where(Deliverable.id == d.id))).scalar_one()
    assert refreshed.verifier_inputs["retry_count"] == 1


@pytest.mark.asyncio
async def test_reclaim_skips_recent_deliverables_within_window(db_session, mock_tenant_id, seeded_tenant) -> None:
    """A deliverable created 10s ago is still within the worker's
    normal processing window — don't retry."""
    await _seed_deliverable(db_session, mock_tenant_id, proof_state=ProofState.verification_missing, age_seconds=10)
    stream_manager = AsyncMock()
    stream_manager.publish = AsyncMock(return_value="0-0")

    n = await reclaim_stale_verifications(db_session, stream_manager, stale_after_s=60, max_retries=3)
    assert n == 0
    stream_manager.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_reclaim_skips_deliverables_without_verifier_type(db_session, mock_tenant_id, seeded_tenant) -> None:
    """If the LLM didn't emit a verification block, ``verifier_type`` is
    None and there's nothing to retry — that's a deliberate "no proof"
    deliverable, not a dropped one."""
    await _seed_deliverable(
        db_session,
        mock_tenant_id,
        proof_state=ProofState.verification_missing,
        verifier_type=None,
        age_seconds=120,
    )
    stream_manager = AsyncMock()
    stream_manager.publish = AsyncMock(return_value="0-0")

    n = await reclaim_stale_verifications(db_session, stream_manager, stale_after_s=60, max_retries=3)
    assert n == 0


@pytest.mark.asyncio
async def test_reclaim_skips_deliverables_at_max_retries(db_session, mock_tenant_id, seeded_tenant) -> None:
    """After max_retries attempts, give up — don't loop forever on a
    genuinely broken deliverable."""
    await _seed_deliverable(
        db_session,
        mock_tenant_id,
        proof_state=ProofState.verification_missing,
        age_seconds=120,
        retry_count=3,
    )
    stream_manager = AsyncMock()
    stream_manager.publish = AsyncMock(return_value="0-0")

    n = await reclaim_stale_verifications(db_session, stream_manager, stale_after_s=60, max_retries=3)
    assert n == 0
    stream_manager.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_reclaim_skips_already_verified_or_failed(db_session, mock_tenant_id, seeded_tenant) -> None:
    """Terminal proof states (verified, verification_failed) are
    end-of-line — don't re-enqueue them."""
    await _seed_deliverable(db_session, mock_tenant_id, proof_state=ProofState.verified, age_seconds=120)
    await _seed_deliverable(
        db_session,
        mock_tenant_id,
        proof_state=ProofState.verification_failed,
        age_seconds=120,
    )
    stream_manager = AsyncMock()
    stream_manager.publish = AsyncMock(return_value="0-0")

    n = await reclaim_stale_verifications(db_session, stream_manager, stale_after_s=60, max_retries=3)
    assert n == 0
