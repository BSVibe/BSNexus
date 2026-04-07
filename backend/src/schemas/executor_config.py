"""Executor configuration schemas."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# Valid executor types
EXECUTOR_TYPES = {"claude_api", "claude_code", "bsgateway", "codex", "generic_llm", "worker"}


class ExecutorConfigCreate(BaseModel):
    name: str
    executor_type: str
    config: dict = Field(default_factory=dict)
    description: Optional[str] = None
    is_default: bool = False


class ExecutorConfigUpdate(BaseModel):
    name: Optional[str] = None
    config: Optional[dict] = None
    description: Optional[str] = None
    is_default: Optional[bool] = None


class ExecutorConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    executor_type: str
    config: dict = Field(default_factory=dict)
    description: Optional[str] = None
    is_default: bool = False
    created_at: datetime
    updated_at: datetime
