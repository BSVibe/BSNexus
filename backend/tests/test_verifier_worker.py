"""VerifierWorker integration tests — Redis Stream consume → state transition.

These mock the Redis stream layer (AsyncMock RedisStreamManager) so the
test runs against the SQLite test DB only, with no live Redis.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from backend.src.core.verifier.protocol import (
    VerificationEnvelope,
    VerificationResult,
    VerifierProofState,
    VerifierType,
)
from backend.src.core.verifier.registry import VerifierRegistry
from backend.src.models import (
    Deliverable,
    DeliverableStatus,
    DeliverableType,
    ProofState,
    Project,
)
from backend.src.workers.verifier_worker import VerifierWorker


# ─── Helpers ────────────────────────────────────────────────────


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
        verifier_type="software_test",
        verifier_inputs={"command": ["true"]},
        **overrides,
    )
    db_session.add(d)
    await db_session.commit()
    await db_session.refresh(d)
    return d


def _envelope_payload(deliverable: Deliverable, message_id: str = "1-0") -> dict:
    return {
        "deliverable_id": str(deliverable.id),
        "tenant_id": str(deliverable.tenant_id),
        "project_id": str(deliverable.project_id),
        "verifier_type": "software_test",
        "inputs": deliverable.verifier_inputs or {},
        "attempt": 1,
        "_message_id": message_id,
    }


class _StubVerifier:
    verifier_types = frozenset({VerifierType.software_test})

    def __init__(self, result: VerificationResult) -> None:
        self.result = result
        self.envelopes: list[VerificationEnvelope] = []

    async def verify(self, envelope: VerificationEnvelope) -> VerificationResult:
        self.envelopes.append(envelope)
        return self.result


def _stream_manager_with_messages(messages: list[dict]) -> AsyncMock:
    """Returns an AsyncMock RedisStreamManager whose ``consume`` yields the
    given messages once, then empty lists thereafter (so the worker loop
    exits via ``stop()``)."""
    sm = AsyncMock()
    sm.redis = AsyncMock()
    sm.redis.xgroup_create = AsyncMock()

    pending = list(messages)

    async def _consume(*_args, **_kwargs):
        if pending:
            return [pending.pop(0)]
        return []

    sm.consume.side_effect = _consume
    sm.acknowledge = AsyncMock()
    sm.publish = AsyncMock(return_value="0-0")
    sm.publish_project_event = AsyncMock()
    return sm


def _session_factory(session_to_yield):
    @asynccontextmanager
    async def _factory():
        yield session_to_yield

    # Worker handles both async_sessionmaker and a callable returning an
    # async context manager. Provide the latter.
    class _Wrapper:
        def __call__(self):
            return _factory()

    return _Wrapper()


async def _drive_worker_one_cycle(worker: VerifierWorker) -> None:
    """Run the worker until it has consumed everything queued, then stop."""
    import asyncio

    task = asyncio.create_task(worker.run_forever())
    # Let the worker drain the queue.
    for _ in range(20):
        await asyncio.sleep(0)
    worker.stop()
    try:
        await asyncio.wait_for(task, timeout=2.0)
    except asyncio.TimeoutError:
        task.cancel()
        with pytest.raises((asyncio.CancelledError, BaseException)):
            await task


# ─── Tests ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_worker_runs_verifier_and_marks_verified(db_session, mock_tenant_id, seeded_tenant) -> None:
    deliverable = await _seed_deliverable(db_session, mock_tenant_id)

    registry = VerifierRegistry()
    verifier = _StubVerifier(
        VerificationResult(
            proof_state=VerifierProofState.verified,
            exit_code=0,
            summary="ok",
            proof_refs=[{"label": "stdout", "type": "log", "href": "inline://stdout"}],
        )
    )
    registry.register(verifier)

    sm = _stream_manager_with_messages([_envelope_payload(deliverable)])
    worker = VerifierWorker(
        registry=registry,
        stream_manager=sm,
        session_factory=_session_factory(db_session),
        block_ms=10,
    )

    await _drive_worker_one_cycle(worker)

    await db_session.refresh(deliverable)
    assert deliverable.proof_state == ProofState.verified
    assert deliverable.verification_exit_code == 0
    assert deliverable.proof_summary == "ok"
    assert deliverable.verified_at is not None

    # Verifier was invoked exactly once with the right envelope.
    assert len(verifier.envelopes) == 1
    assert verifier.envelopes[0].deliverable_id == deliverable.id

    # Two SSE events: verifying → verified.
    sm.publish_project_event.assert_awaited()
    events = [c.args[1] for c in sm.publish_project_event.await_args_list]
    assert events == ["deliverable_proof", "deliverable_proof"]

    # Message acknowledged so it doesn't redeliver.
    sm.acknowledge.assert_awaited()


@pytest.mark.asyncio
async def test_worker_marks_failed_when_verifier_returns_failure(
    db_session, mock_tenant_id, seeded_tenant
) -> None:
    deliverable = await _seed_deliverable(db_session, mock_tenant_id)

    registry = VerifierRegistry()
    registry.register(
        _StubVerifier(
            VerificationResult(
                proof_state=VerifierProofState.verification_failed,
                exit_code=2,
                summary="exit=2 · pytest 1 failed",
            )
        )
    )

    sm = _stream_manager_with_messages([_envelope_payload(deliverable)])
    worker = VerifierWorker(
        registry=registry,
        stream_manager=sm,
        session_factory=_session_factory(db_session),
        block_ms=10,
    )
    await _drive_worker_one_cycle(worker)

    await db_session.refresh(deliverable)
    assert deliverable.proof_state == ProofState.verification_failed
    assert deliverable.verification_exit_code == 2
    assert deliverable.verified_at is None


@pytest.mark.asyncio
async def test_worker_handles_unknown_verifier_type(db_session, mock_tenant_id, seeded_tenant) -> None:
    """Envelope's verifier_type isn't registered → the worker leaves
    proof_state at ``verification_missing`` (we never actually verified)
    but populates ``proof_summary`` so the UI can surface the gap."""
    deliverable = await _seed_deliverable(db_session, mock_tenant_id)

    sm = _stream_manager_with_messages([_envelope_payload(deliverable)])
    worker = VerifierWorker(
        registry=VerifierRegistry(),  # empty → no handler for software_test
        stream_manager=sm,
        session_factory=_session_factory(db_session),
        block_ms=10,
    )
    await _drive_worker_one_cycle(worker)

    await db_session.refresh(deliverable)
    assert deliverable.proof_state == ProofState.verification_missing
    assert "No verifier registered" in (deliverable.proof_summary or "")
    sm.acknowledge.assert_awaited()


@pytest.mark.asyncio
async def test_worker_traps_verifier_exceptions(db_session, mock_tenant_id, seeded_tenant) -> None:
    """Verifier contract says ``verify`` shouldn't raise. If it does
    anyway, the worker turns it into a verification_failed transition."""
    deliverable = await _seed_deliverable(db_session, mock_tenant_id)

    class _BoomVerifier:
        verifier_types = frozenset({VerifierType.software_test})

        async def verify(self, envelope):  # noqa: ARG002
            raise RuntimeError("kaboom")

    registry = VerifierRegistry()
    registry.register(_BoomVerifier())

    sm = _stream_manager_with_messages([_envelope_payload(deliverable)])
    worker = VerifierWorker(
        registry=registry,
        stream_manager=sm,
        session_factory=_session_factory(db_session),
        block_ms=10,
    )
    await _drive_worker_one_cycle(worker)

    await db_session.refresh(deliverable)
    assert deliverable.proof_state == ProofState.verification_failed
    assert "kaboom" in (deliverable.proof_summary or "")


@pytest.mark.asyncio
async def test_worker_skips_missing_deliverable(db_session, mock_tenant_id, seeded_tenant) -> None:
    """Envelope references a deliverable that doesn't exist (already
    deleted, foreign tenant). Worker logs + ack's, never crashes."""
    sm = _stream_manager_with_messages(
        [
            {
                "deliverable_id": str(uuid.uuid4()),
                "tenant_id": str(mock_tenant_id),
                "project_id": str(uuid.uuid4()),
                "verifier_type": "software_test",
                "inputs": {},
                "attempt": 1,
                "_message_id": "1-0",
            }
        ]
    )
    registry = VerifierRegistry()
    registry.register(_StubVerifier(VerificationResult(proof_state=VerifierProofState.verified)))

    worker = VerifierWorker(
        registry=registry,
        stream_manager=sm,
        session_factory=_session_factory(db_session),
        block_ms=10,
    )

    await _drive_worker_one_cycle(worker)

    sm.acknowledge.assert_awaited()  # still acked so it doesn't redeliver
