from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from backend.src.core.domain import (
    BriefScope,
    DeliverableStatus,
    DeliverableType,
    DirectionSource,
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
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DirectionAckResponse(BaseModel):
    direction: DirectionResponse
    request: RequestResponse | None
    routing: DirectionRoutingPrompt | None
    acknowledgement: str


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
    status: DeliverableStatus
    risk_summary: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BriefSnapshotResponse(BaseModel):
    scope: BriefScope
    project_id: uuid.UUID | None
    sections: dict[str, list]
    generated_at: datetime
