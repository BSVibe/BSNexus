from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.src.core.domain import (
    BriefScope,
    DeliverableStatus,
    DeliverableType,
    DirectionSource,
    ProofAspectStatus,
    ProofAspectType,
    ProofState,
    RequestStatus,
)


class DirectionCreate(BaseModel):
    project_id: uuid.UUID | None = None
    source: DirectionSource
    body: str = Field(..., min_length=1)
    target_hint: str | None = None

    model_config = ConfigDict(extra="forbid")


class DirectionResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    project_id: uuid.UUID | None
    source: DirectionSource
    actor_id: str
    body: str
    target_hint: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DirectionRoutingOption(BaseModel):
    project_id: uuid.UUID
    name: str


class DirectionRoutingPrompt(BaseModel):
    required: bool = True
    question: str
    options: list[DirectionRoutingOption]


class RequestResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    project_id: uuid.UUID
    origin_direction_id: uuid.UUID | None
    intent: str
    status: RequestStatus
    current_step_id: uuid.UUID | None
    last_brief_id: uuid.UUID | None
    pr_number: int | None = None
    pr_url: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DirectionAckResponse(BaseModel):
    direction: DirectionResponse
    request: RequestResponse | None
    routing: DirectionRoutingPrompt | None


class DecisionResolve(BaseModel):
    # Forward-only — there is deliberately no ``abandon``. ``retry``
    # re-dispatches the work as-is; ``reframe`` re-dispatches it with
    # the founder's free-text ``guidance`` seeded as added direction.
    resolution: Literal["retry", "reframe"]
    # Optional — the resolve handler derives ``resolved_by`` from the
    # authenticated user when the payload omits it (the founder UI sends
    # only ``{resolution[, guidance]}``). An explicit value still wins.
    resolved_by: str | None = Field(default=None, min_length=1)
    guidance: str | None = Field(default=None, max_length=4000)

    model_config = ConfigDict(extra="forbid")


class DecisionResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    project_id: uuid.UUID
    request_id: uuid.UUID | None
    work_step_id: uuid.UUID | None
    question: str
    options: list
    blocking: bool
    resolved_at: datetime | None
    resolution: str | None
    resolved_by: str | None
    guidance: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DeliverableCreate(BaseModel):
    project_id: uuid.UUID
    request_id: uuid.UUID | None = None
    work_step_id: uuid.UUID | None = None
    type: DeliverableType = DeliverableType.code
    title: str = Field(..., min_length=1)
    summary: str | None = None
    artifact_refs: list = Field(default_factory=list)
    risk_summary: str | None = None

    model_config = ConfigDict(extra="forbid")


class ProofAspectResponse(BaseModel):
    id: uuid.UUID
    aspect_type: ProofAspectType
    status: ProofAspectStatus
    exit_code: int | None = None
    summary: str | None = None
    completed_at: datetime | None = None
    blocking: bool


class ProofStatusResponse(BaseModel):
    state: ProofState
    aspects: list[ProofAspectResponse] = []
    latest_test_status: ProofAspectStatus | None = None
    latest_test_summary: str | None = None
    latest_test_completed_at: datetime | None = None


class DeliverableResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    project_id: uuid.UUID
    request_id: uuid.UUID | None
    work_step_id: uuid.UUID | None
    type: DeliverableType
    title: str
    summary: str | None
    artifact_refs: list
    proof_state: ProofState
    proof_status: ProofStatusResponse | None = None
    status: DeliverableStatus
    risk_summary: str | None
    commit_sha: str | None = None
    diff_url: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BriefDeliverableCard(BaseModel):
    """Brief shipped/blocked deliverable shape — typed sibling of frontend
    ``BriefDeliverable``. Joins the Deliverable row with the latest
    ProofAttempt for verifier_type / proof_summary / verified_at."""

    id: uuid.UUID
    project_id: uuid.UUID
    request_id: uuid.UUID | None
    title: str
    type: DeliverableType
    proof_state: ProofState
    proof_summary: str | None = None
    verifier_type: str | None = None
    verified_at: datetime | None = None
    created_at: datetime
    artifact_refs: list = Field(default_factory=list)
    commit_sha: str | None = None
    diff_url: str | None = None


class BriefDecisionCard(BaseModel):
    """Brief needs_decision item — typed sibling of frontend ``BriefDecision``."""

    id: uuid.UUID
    project_id: uuid.UUID
    question: str
    blocking: bool
    created_at: datetime


class BriefRequestCard(BaseModel):
    """Brief running-request item — typed sibling of frontend
    ``BriefRequest``. Backed by the Request row directly; greenfield Brief
    surfaces Requests, not legacy ExecutionRuns."""

    id: uuid.UUID
    project_id: uuid.UUID
    intent: str
    status: RequestStatus
    created_at: datetime
    updated_at: datetime
    # G8.3 — surface the bound GitHub PR right on the Brief card so the
    # founder can jump from a running/blocked request to the in-flight
    # PR without drilling into the Request detail.
    pr_number: int | None = None
    pr_url: str | None = None


class BriefBlockedDeliverableCard(BriefDeliverableCard):
    # The ``blocked`` section is now deliverable-only — the
    # ``RequestStatus.blocked`` dead-end is retired; a stalled Request
    # waits in ``needs_decision`` and surfaces there via its open
    # founder Decision. ``kind`` is retained so the frontend's existing
    # discriminated-union narrowing keeps compiling.
    kind: Literal["deliverable"] = "deliverable"


BriefBlockedItem = BriefBlockedDeliverableCard


class BriefNextHint(BaseModel):
    """AI-recommended next direction. Empty until the recommendation
    surface lands; see file-disposition.md REVIEW_LATER list."""

    summary: str
    request_id: uuid.UUID | None = None


class BriefSections(BaseModel):
    shipped: list[BriefDeliverableCard]
    needs_decision: list[BriefDecisionCard]
    blocked: list[BriefBlockedItem]
    running: list[BriefRequestCard]
    next: list[BriefNextHint]


class BriefSnapshotResponse(BaseModel):
    scope: BriefScope
    project_id: uuid.UUID | None
    sections: BriefSections
    generated_at: datetime
