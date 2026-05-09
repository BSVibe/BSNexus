from __future__ import annotations

from backend.src.core.domain import (
    ProofState,
    RequestStatus,
    RunAttemptPhase,
    WorkStepStatus,
)


REQUEST_TRANSITIONS: dict[RequestStatus, set[RequestStatus]] = {
    RequestStatus.open: {RequestStatus.running, RequestStatus.abandoned},
    RequestStatus.running: {
        RequestStatus.blocked,
        RequestStatus.review_ready,
        RequestStatus.abandoned,
    },
    RequestStatus.blocked: {RequestStatus.running, RequestStatus.abandoned},
    RequestStatus.review_ready: {RequestStatus.running, RequestStatus.shipped},
    RequestStatus.shipped: set(),
    RequestStatus.abandoned: set(),
}

WORK_STEP_TRANSITIONS: dict[WorkStepStatus, set[WorkStepStatus]] = {
    WorkStepStatus.pending: {WorkStepStatus.running, WorkStepStatus.skipped},
    WorkStepStatus.running: {
        WorkStepStatus.needs_decision,
        WorkStepStatus.verifying,
        WorkStepStatus.failed,
    },
    WorkStepStatus.needs_decision: {WorkStepStatus.running, WorkStepStatus.skipped},
    WorkStepStatus.verifying: {WorkStepStatus.review_ready, WorkStepStatus.failed},
    WorkStepStatus.review_ready: set(),
    WorkStepStatus.failed: set(),
    WorkStepStatus.skipped: set(),
}

RUN_ATTEMPT_PHASE_ORDER = (
    RunAttemptPhase.prepare,
    RunAttemptPhase.work,
    RunAttemptPhase.verify,
    RunAttemptPhase.summarize,
    RunAttemptPhase.terminal,
)

PROOF_TRANSITIONS: dict[ProofState, set[ProofState]] = {
    ProofState.verification_missing: {
        ProofState.verifying,
        ProofState.human_review_required,
    },
    ProofState.verifying: {
        ProofState.verified,
        ProofState.verification_failed,
        ProofState.human_review_required,
    },
    ProofState.verification_failed: {ProofState.verifying},
    ProofState.human_review_required: {ProofState.verified},
    ProofState.verified: set(),
}


def can_transition_request(current: RequestStatus, target: RequestStatus) -> bool:
    return target in REQUEST_TRANSITIONS[current]


def can_transition_proof(current: ProofState, target: ProofState) -> bool:
    return target in PROOF_TRANSITIONS[current]
