"""VerifierStateMachine — transitions Deliverable.proof_state.

Disjoint from ``RunStateMachine``. The valid transitions are:

    verification_missing → verifying → verified
                             ↓
                          verification_failed → verifying  (retry)
    verification_missing → not_applicable
    verification_missing → human_review_required
    verifying → human_review_required

Every transition is the single source of truth for stamping the
Deliverable's proof fields:

- ``proof_state``
- ``verified_at`` (only on terminal-good states)
- ``verification_exit_code``, ``proof_summary``, ``proof_refs``,
  ``risk_summary`` (from the ``VerificationResult``)

The state machine also publishes an SSE ``deliverable_proof`` event onto
the project events stream so the Brief / Decision Inbox / Deliverables
surface can update without polling.

Audit emit for proof transitions is not wired in PR3 — bsvibe-audit
only defines ``nexus.deliverable.created`` today. A follow-up adds
``nexus.deliverable.verified`` / ``.verification_failed`` and plugs them
in here.
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.verifier.protocol import VerificationResult, VerifierProofState
from backend.src.models import Deliverable, ProofState
from backend.src.queue.streams import RedisStreamManager

logger = structlog.get_logger(__name__)


# Mapping helper — model enum and protocol enum are kept in lock-step but
# carry their own type so the protocol layer stays decoupled from the
# SQLAlchemy model.
_PROOF_STATE_TO_MODEL: dict[VerifierProofState, ProofState] = {
    VerifierProofState.verification_missing: ProofState.verification_missing,
    VerifierProofState.verifying: ProofState.verifying,
    VerifierProofState.verified: ProofState.verified,
    VerifierProofState.verification_failed: ProofState.verification_failed,
    VerifierProofState.human_review_required: ProofState.human_review_required,
    VerifierProofState.not_applicable: ProofState.not_applicable,
}


class InvalidProofTransitionError(ValueError):
    """Raised when a caller attempts an illegal proof_state transition."""


class VerifierStateMachine:
    TRANSITIONS: dict[ProofState, set[ProofState]] = {
        ProofState.verification_missing: {
            ProofState.verifying,
            ProofState.not_applicable,
            ProofState.human_review_required,
        },
        ProofState.verifying: {
            ProofState.verified,
            ProofState.verification_failed,
            ProofState.human_review_required,
        },
        ProofState.verified: set(),
        ProofState.verification_failed: {ProofState.verifying},
        ProofState.human_review_required: {
            ProofState.verifying,
            ProofState.verified,
            ProofState.verification_failed,
        },
        ProofState.not_applicable: set(),
    }

    def can_transition(self, from_state: ProofState, to_state: ProofState) -> bool:
        return to_state in self.TRANSITIONS.get(from_state, set())

    async def transition(
        self,
        deliverable: Deliverable,
        new_state: ProofState,
        *,
        result: VerificationResult | None = None,
        db_session: AsyncSession | None = None,
        stream_manager: RedisStreamManager | None = None,
    ) -> Deliverable:
        old_state = deliverable.proof_state

        if old_state == new_state:
            # Idempotent — repeat transition (e.g. retry hits the same
            # terminal state) is a no-op, not an error. The Deliverable
            # stays untouched.
            return deliverable

        if not self.can_transition(old_state, new_state):
            logger.error(
                "invalid_proof_transition",
                deliverable_id=str(deliverable.id),
                from_state=old_state.value,
                to_state=new_state.value,
            )
            raise InvalidProofTransitionError(f"Invalid proof_state transition: {old_state.value} → {new_state.value}")

        deliverable.proof_state = new_state

        if result is not None:
            if result.exit_code is not None:
                deliverable.verification_exit_code = result.exit_code
            if result.summary is not None:
                deliverable.proof_summary = result.summary
            if result.proof_refs:
                deliverable.proof_refs = list(result.proof_refs)
            if result.risk_summary is not None:
                deliverable.risk_summary = result.risk_summary

        if new_state == ProofState.verified:
            deliverable.verified_at = datetime.now(timezone.utc)

        if db_session is not None:
            await db_session.flush()

        logger.info(
            "proof_transition",
            deliverable_id=str(deliverable.id),
            from_state=old_state.value,
            to_state=new_state.value,
            exit_code=deliverable.verification_exit_code,
        )

        if stream_manager is not None:
            await stream_manager.publish_project_event(
                str(deliverable.project_id),
                "deliverable_proof",
                {
                    "deliverable_id": str(deliverable.id),
                    "from_state": old_state.value,
                    "to_state": new_state.value,
                    "exit_code": deliverable.verification_exit_code,
                    "proof_summary": deliverable.proof_summary,
                    "verifier_type": deliverable.verifier_type,
                },
            )

        return deliverable


def to_model_state(state: VerifierProofState) -> ProofState:
    """Translate from the protocol-layer enum to the SQLAlchemy model enum."""
    return _PROOF_STATE_TO_MODEL[state]
