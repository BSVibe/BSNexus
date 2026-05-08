"""Brief endpoint schemas — single-call payload composing 5 sections.

Decision-locks **A2** (locked 2026-05-08) — every interface (web, mobile,
Slack, email, voice) consumes the same shape, so each section ships with
just the fields the founder card needs (no full-row responses, no extra
joins on the client).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from backend.src.models import (
    DeliverableType,
    ProofState,
    RunStatus,
)


class BriefDeliverable(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    type: DeliverableType
    proof_state: ProofState
    proof_summary: str | None
    verifier_type: str | None
    verified_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BriefDecision(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    question: str
    blocking: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BriefRun(BaseModel):
    """A run that is either blocked or actively running.

    ``request_intent`` carries the founder-visible intent so the card
    doesn't have to make a follow-up request. ``error_message`` is only
    populated for blocked runs.
    """

    id: uuid.UUID
    project_id: uuid.UUID
    request_id: uuid.UUID | None
    request_intent: str | None
    status: RunStatus
    started_at: datetime | None
    created_at: datetime
    error_message: str | None

    model_config = ConfigDict(from_attributes=True)


class BriefNextHint(BaseModel):
    """Recommended next direction.

    Empty in PR4 (the LLM-side derivation lands in a follow-up). Frozen as
    a typed list now so the contract doesn't move when hints turn on.
    """

    summary: str
    request_id: uuid.UUID | None = None


class BriefResponse(BaseModel):
    project_id: uuid.UUID | None
    generated_at: datetime
    shipped: list[BriefDeliverable]
    needs_decision: list[BriefDecision]
    blocked: list[BriefRun]
    running: list[BriefRun]
    next: list[BriefNextHint]
