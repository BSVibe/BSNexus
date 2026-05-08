"""Verifier core unit tests — protocol, registry, subprocess verifier.

These exercise the layer that doesn't touch the DB. State-machine and
worker integration live in their own test files.
"""

from __future__ import annotations

import sys
import uuid

import pytest

from backend.src.core.verifier import (
    SubprocessVerifier,
    VerificationEnvelope,
    VerificationResult,
    Verifier,
    VerifierNotRegisteredError,
    VerifierProofState,
    VerifierRegistry,
    VerifierType,
)


# ─── Envelope round-trip ───────────────────────────────────────────────


def test_envelope_round_trips_through_payload() -> None:
    envelope = VerificationEnvelope(
        deliverable_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        verifier_type=VerifierType.software_test,
        inputs={"command": ["pytest"], "timeout_s": 60},
        attempt=2,
    )
    payload = envelope.to_payload()
    restored = VerificationEnvelope.from_payload(payload)
    assert restored == envelope


def test_envelope_from_payload_defaults_attempt_when_missing() -> None:
    payload = {
        "deliverable_id": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "project_id": str(uuid.uuid4()),
        "verifier_type": "software_test",
        "inputs": {},
    }
    envelope = VerificationEnvelope.from_payload(payload)
    assert envelope.attempt == 1


# ─── Registry ──────────────────────────────────────────────────────────


class _FakeVerifier:
    def __init__(self, types: frozenset[VerifierType]) -> None:
        self.verifier_types = types

    async def verify(self, envelope: VerificationEnvelope) -> VerificationResult:  # noqa: ARG002
        return VerificationResult(proof_state=VerifierProofState.verified)


def test_registry_routes_by_verifier_type() -> None:
    registry = VerifierRegistry()
    a = _FakeVerifier(frozenset({VerifierType.software_test}))
    b = _FakeVerifier(frozenset({VerifierType.software_build}))
    registry.register(a)
    registry.register(b)

    assert registry.resolve(VerifierType.software_test) is a
    assert registry.resolve(VerifierType.software_build) is b


def test_registry_raises_on_missing_type() -> None:
    registry = VerifierRegistry()
    with pytest.raises(VerifierNotRegisteredError):
        registry.resolve(VerifierType.software_test)


def test_registry_register_rejects_empty_types() -> None:
    registry = VerifierRegistry()
    empty = _FakeVerifier(frozenset())
    with pytest.raises(ValueError):
        registry.register(empty)


def test_registry_supported_types_reflects_registrations() -> None:
    registry = VerifierRegistry()
    registry.register(
        _FakeVerifier(frozenset({VerifierType.software_test, VerifierType.software_start}))
    )
    assert registry.supported_types() == frozenset(
        {VerifierType.software_test, VerifierType.software_start}
    )


def test_subprocess_verifier_implements_verifier_protocol() -> None:
    """``SubprocessVerifier`` must satisfy the runtime-checkable Protocol
    so the registry's open type set holds at runtime, not just in
    annotations."""
    assert isinstance(SubprocessVerifier(), Verifier)


# ─── SubprocessVerifier — happy / fail / missing-cmd / timeout ─────────


def _envelope(verifier_type: VerifierType = VerifierType.software_test, **inputs) -> VerificationEnvelope:
    return VerificationEnvelope(
        deliverable_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        verifier_type=verifier_type,
        inputs=inputs,
    )


@pytest.mark.asyncio
async def test_subprocess_verifier_success_marks_verified() -> None:
    verifier = SubprocessVerifier()
    envelope = _envelope(command=[sys.executable, "-c", "print('ok')"])

    result = await verifier.verify(envelope)

    assert result.proof_state == VerifierProofState.verified
    assert result.exit_code == 0
    assert "ok" in (result.summary or "")
    assert any(ref["label"] == "stdout" for ref in result.proof_refs)


@pytest.mark.asyncio
async def test_subprocess_verifier_nonzero_exit_marks_failed() -> None:
    verifier = SubprocessVerifier()
    envelope = _envelope(command=[sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(2)"])

    result = await verifier.verify(envelope)

    assert result.proof_state == VerifierProofState.verification_failed
    assert result.exit_code == 2
    assert "exit=2" in (result.summary or "")
    assert any(ref["label"] == "stderr" for ref in result.proof_refs)


@pytest.mark.asyncio
async def test_subprocess_verifier_missing_command_fails_cleanly() -> None:
    verifier = SubprocessVerifier()
    envelope = _envelope()  # no command

    result = await verifier.verify(envelope)

    assert result.proof_state == VerifierProofState.verification_failed
    assert "No command" in (result.summary or "")


@pytest.mark.asyncio
async def test_subprocess_verifier_unknown_executable_fails_cleanly() -> None:
    verifier = SubprocessVerifier()
    envelope = _envelope(command=["/no/such/binary/at/all"])

    result = await verifier.verify(envelope)

    assert result.proof_state == VerifierProofState.verification_failed
    assert result.exit_code is None  # spawn failure — no process ran
    assert "Failed to spawn" in (result.summary or "")


@pytest.mark.asyncio
async def test_subprocess_verifier_timeout_kills_process_and_reports_failure() -> None:
    verifier = SubprocessVerifier()
    envelope = _envelope(
        command=[sys.executable, "-c", "import time; time.sleep(5)"],
        timeout_s=1,
    )

    result = await verifier.verify(envelope)

    assert result.proof_state == VerifierProofState.verification_failed
    assert "timed out" in (result.summary or "")


@pytest.mark.asyncio
async def test_subprocess_verifier_accepts_string_command() -> None:
    """Convenience: a string command shlex-splits the same as the LLM
    would produce."""
    verifier = SubprocessVerifier()
    envelope = _envelope(command=f"{sys.executable} -c 'print(42)'")

    result = await verifier.verify(envelope)

    assert result.proof_state == VerifierProofState.verified
    assert "42" in (result.summary or "")


@pytest.mark.asyncio
async def test_subprocess_verifier_caps_timeout_to_max() -> None:
    """An LLM-supplied 30000s timeout is silently capped to the verifier's
    hard ceiling so the worker can't be parked forever."""
    verifier = SubprocessVerifier()
    envelope = _envelope(command=[sys.executable, "-c", "print('quick')"], timeout_s=30000)

    # Just verify it runs to completion under the cap. The actual cap is an
    # internal invariant; this test pins it doesn't reject the field.
    result = await verifier.verify(envelope)
    assert result.proof_state == VerifierProofState.verified
