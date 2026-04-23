"""Worker + install-token schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WorkerResponse(BaseModel):
    """API-safe view of a Worker — no ``token_hash``."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    labels: list[str]
    status: str
    last_heartbeat: datetime | None
    capabilities: list[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InstallTokenStatus(BaseModel):
    has_token: bool


class InstallTokenCreated(BaseModel):
    has_token: bool
    token: str  # shown only once at generation time


class WorkerRegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    capabilities: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)


class WorkerRegisterResponse(BaseModel):
    """Returned once to the worker binary. ``token`` is the long-lived
    worker token — server only stores its SHA-256 hash."""

    id: uuid.UUID
    token: str


class WorkerResultRequest(BaseModel):
    task_id: uuid.UUID
    success: bool
    output_data: dict[str, Any] | None = None
    error_message: str | None = None
