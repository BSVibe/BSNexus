"""Verifier Worker — type-routed proof generator for Deliverables.

Architecture decision **A1** in ``~/Docs/BSNexus/planning/decision-locks.md``
locked this in 2026-05-08. The worker is a separate process from the LLM
run dispatcher: when an ExecutionRun creates a Deliverable that ships
with a ``verification_command`` (or any verifier inputs) the orchestrator
enqueues an envelope on the ``verification:queue`` Redis Stream. A
``VerifierWorker`` consumer picks it up, looks up the ``Verifier``
implementation matching the envelope's ``verifier_type``, and asks it to
verify the deliverable. The result is stamped onto the Deliverable via
``VerifierStateMachine``.

The registry shape is open from day one so that as BSNexus expands beyond
the AI-native software company slice, design / doc / marketing / preview
verifiers slot in as new ``Verifier`` implementations against the same
worker, queue envelope, and SSE channel — no parallel infrastructure.

PR3 ships only ``SubprocessVerifier`` (for ``software_test`` /
``software_build`` / ``software_start`` types).
"""

from __future__ import annotations

from backend.src.core.verifier.protocol import (
    VerificationEnvelope,
    VerificationResult,
    Verifier,
    VerifierProofState,
    VerifierType,
)
from backend.src.core.verifier.registry import (
    VerifierNotRegisteredError,
    VerifierRegistry,
    default_registry,
)
from backend.src.core.verifier.subprocess_verifier import SubprocessVerifier

__all__ = [
    "SubprocessVerifier",
    "VerificationEnvelope",
    "VerificationResult",
    "Verifier",
    "VerifierNotRegisteredError",
    "VerifierProofState",
    "VerifierRegistry",
    "VerifierType",
    "default_registry",
]
