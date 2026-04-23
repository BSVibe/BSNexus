"""Worker + install-token schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


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
