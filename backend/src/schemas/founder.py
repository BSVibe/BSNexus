"""Schemas for Request / Deliverable / Decision / ExecutionRun / CompositionSnapshot."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from backend.src.models import (
    CompositionSource,
    DeliverableStatus,
    DeliverableType,
    ProofState,
    RequestStatus,
    RunPriority,
    RunStatus,
    StorageBackend,
)


# ─── Request ────────────────────────────────────────────────────


class RequestResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    project_id: uuid.UUID
    origin_message_id: uuid.UUID | None
    intent_summary: str
    status: RequestStatus
    user_confirmed: bool
    superseded_by_id: uuid.UUID | None
    composition_root_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ─── Deliverable ────────────────────────────────────────────────


class DeliverableVersionResponse(BaseModel):
    id: uuid.UUID
    deliverable_id: uuid.UUID
    version_int: int
    storage_backend: StorageBackend
    content_ref: dict[str, Any]
    content_hash: str
    size_bytes: int | None
    created_by_run_id: uuid.UUID | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DeliverableResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    project_id: uuid.UUID
    request_id: uuid.UUID | None
    type: DeliverableType
    title: str
    status: DeliverableStatus
    current_version_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    # Proof model (decision-locks A1) — see docs/BSNexus/product/06-verified-deliverables-and-proof.md
    proof_state: ProofState
    verifier_type: str | None = None
    verifier_inputs: dict | None = None
    verification_exit_code: int | None = None
    proof_summary: str | None = None
    proof_refs: list | None = None
    risk_summary: str | None = None
    verified_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class DeliverableWithCurrentVersion(DeliverableResponse):
    current_version: DeliverableVersionResponse | None = None


# ─── Decision ───────────────────────────────────────────────────


class DecisionResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    project_id: uuid.UUID
    request_id: uuid.UUID | None
    origin_run_id: uuid.UUID | None
    question: str
    options: list[str]
    blocking: bool
    resolved_at: datetime | None
    resolution: str | None
    resolved_by: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DecisionResolve(BaseModel):
    resolution: str
    resolved_by: str | None = None

    model_config = ConfigDict(extra="forbid")


# ─── ExecutionRun (Inside panel) ────────────────────────────────


class ExecutionRunResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    project_id: uuid.UUID
    request_id: uuid.UUID
    parent_run_id: uuid.UUID | None
    composition_snapshot_id: uuid.UUID | None
    status: RunStatus
    priority: RunPriority
    directive: str | None = None
    output_type: str | None
    output_ref: dict[str, Any] | None
    estimated_cost_cents: int
    actual_cost_cents: int
    branch_name: str | None
    commit_hash: str | None
    error_message: str | None
    retry_count: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


# ─── CompositionSnapshot (Inside panel) ─────────────────────────


class CompositionSnapshotResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    request_id: uuid.UUID
    execution_run_id: uuid.UUID | None
    source: CompositionSource
    bsage_composition_id: str | None
    system_prompt_ref: dict[str, Any]
    tools_allowed: list[str]
    context_doc_refs: list[dict[str, Any]]
    persona_label: str
    fit_score: float | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
