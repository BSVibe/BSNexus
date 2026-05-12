from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.src.core.domain import (
    BriefScope,
    DeliverableStatus,
    DeliverableType,
    DirectionSource,
    ProofAttemptStatus,
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
    resolution: str = Field(..., min_length=1)
    resolved_by: str = Field(..., min_length=1)

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


class ProofStatusResponse(BaseModel):
    state: ProofState
    policy_id: uuid.UUID | None
    latest_attempt_id: uuid.UUID | None = None
    latest_attempt_status: ProofAttemptStatus | None = None
    latest_attempt_summary: str | None = None
    latest_attempt_completed_at: datetime | None = None


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
    proof_policy_id: uuid.UUID | None
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
    """Brief running/blocked-request item — typed sibling of frontend
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


class BriefBlockedRequestCard(BriefRequestCard):
    kind: Literal["request"] = "request"


class BriefBlockedDeliverableCard(BriefDeliverableCard):
    kind: Literal["deliverable"] = "deliverable"


# Pydantic discriminated union — frontend narrows via ``kind``.
BriefBlockedItem = Annotated[
    BriefBlockedRequestCard | BriefBlockedDeliverableCard,
    Field(discriminator="kind"),
]


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
