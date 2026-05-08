"""Verifier Protocol — what every verifier implementation honors.

The shape is intentionally minimal:

- ``VerificationEnvelope`` is what gets enqueued on the Redis Stream and
  what the worker hands to a verifier. It carries the deliverable id,
  tenant/project scope (so the verifier can stay tenant-scoped on any
  filesystem / artifact reads), the ``verifier_type`` discriminant for
  registry lookup, and verifier-specific inputs (``command``, ``cwd``,
  ``timeout_s`` for the subprocess verifier; future verifiers add their
  own fields without touching the queue protocol — they live in
  ``inputs``).

- ``VerificationResult`` is what a verifier returns. The state machine
  reads ``proof_state``, ``exit_code``, ``summary``, and ``proof_refs``
  off it and transitions the Deliverable.

- ``Verifier`` is a Protocol so implementations stay loose; subclassing
  is not required.

A verifier is responsible for:

- enforcing its own timeout / sandbox / resource caps;
- never raising out of ``verify()`` — verifier failure is a
  ``proof_state = verification_failed`` result, not an exception. The
  worker's outer wrapper still catches stragglers, but verifiers should
  return clean results so the audit trail and ``proof_summary`` are
  meaningful.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class VerifierProofState(str, enum.Enum):
    """States a Deliverable's proof can be in.

    Mirrors the ``proof_state`` column on ``Deliverable``. Disjoint from
    ``RunStatus`` — verification is its own pipeline.
    """

    verification_missing = "verification_missing"
    verifying = "verifying"
    verified = "verified"
    verification_failed = "verification_failed"
    human_review_required = "human_review_required"
    not_applicable = "not_applicable"


class VerifierType(str, enum.Enum):
    """Discriminant for routing envelopes to a verifier implementation.

    Open enum on purpose — adding ``design_screenshot`` / ``doc_checklist``
    / ``marketing_channel_checklist`` / ``preview_url`` / ``pr_diff``
    later is a one-line addition here plus a registry entry. Existing
    verifiers do not need to change.
    """

    software_test = "software_test"
    software_build = "software_build"
    software_start = "software_start"


@dataclass(slots=True)
class VerificationEnvelope:
    """Enqueued payload — what the worker pulls off the stream.

    All fields are JSON-serializable so the envelope round-trips through
    Redis Streams cleanly.
    """

    deliverable_id: uuid.UUID
    tenant_id: uuid.UUID
    project_id: uuid.UUID
    verifier_type: VerifierType
    inputs: dict[str, Any] = field(default_factory=dict)
    attempt: int = 1

    def to_payload(self) -> dict[str, Any]:
        return {
            "deliverable_id": str(self.deliverable_id),
            "tenant_id": str(self.tenant_id),
            "project_id": str(self.project_id),
            "verifier_type": self.verifier_type.value,
            "inputs": self.inputs,
            "attempt": self.attempt,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "VerificationEnvelope":
        return cls(
            deliverable_id=uuid.UUID(payload["deliverable_id"]),
            tenant_id=uuid.UUID(payload["tenant_id"]),
            project_id=uuid.UUID(payload["project_id"]),
            verifier_type=VerifierType(payload["verifier_type"]),
            inputs=dict(payload.get("inputs") or {}),
            attempt=int(payload.get("attempt") or 1),
        )


@dataclass(slots=True)
class VerificationResult:
    """Verifier output — read by VerifierStateMachine to stamp the Deliverable."""

    proof_state: VerifierProofState
    exit_code: int | None = None
    summary: str | None = None
    proof_refs: list[dict[str, str]] = field(default_factory=list)
    risk_summary: str | None = None


@runtime_checkable
class Verifier(Protocol):
    """A verifier implementation routed by ``VerifierType``.

    ``verifier_types`` is the set of discriminants this verifier accepts;
    the registry uses it to route envelopes. ``verify()`` must not raise:
    return a ``VerificationResult`` with the appropriate failure state
    instead.
    """

    verifier_types: frozenset[VerifierType]

    async def verify(self, envelope: VerificationEnvelope) -> VerificationResult: ...
