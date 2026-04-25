"""Executor configuration schemas."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# Valid executor types
EXECUTOR_TYPES = {"generic_llm", "claude_code", "bsgateway", "codex", "worker"}


class ExecutorConfigCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    executor_type: str
    config: dict = Field(default_factory=dict)
    description: Optional[str] = None
    is_selected: bool = False


class ExecutorConfigUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    config: Optional[dict] = None
    description: Optional[str] = None
    is_selected: Optional[bool] = None


class ExecutorConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    executor_type: str
    config: dict = Field(default_factory=dict)
    description: Optional[str] = None
    is_selected: bool = False
    created_at: datetime
    updated_at: datetime
