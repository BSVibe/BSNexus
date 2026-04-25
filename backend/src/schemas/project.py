"""Project request/response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field("", max_length=10_000)
    bsage_workspace_id: str | None = None
    bsupervisor_policy_id: str | None = None

    model_config = ConfigDict(extra="forbid")


class ProjectUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=10_000)
    status: str | None = None
    bsage_workspace_id: str | None = None
    bsupervisor_policy_id: str | None = None

    model_config = ConfigDict(extra="forbid")


class ProjectResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    description: str
    status: str
    bsage_workspace_id: str | None
    bsupervisor_policy_id: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
