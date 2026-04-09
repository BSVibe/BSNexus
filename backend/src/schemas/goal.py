"""Goal Pydantic schemas for goal alignment cascade."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class GoalCreate(BaseModel):
    title: str
    description: Optional[str] = None
    level: str = "project"  # "mission", "department", "project", "task"
    parent_goal_id: Optional[uuid.UUID] = None
    project_id: Optional[uuid.UUID] = None
    agent_id: Optional[uuid.UUID] = None


class GoalUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    level: Optional[str] = None
    parent_goal_id: Optional[uuid.UUID] = None


class GoalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    parent_goal_id: Optional[uuid.UUID] = None
    level: str
    title: str
    description: Optional[str] = None
    project_id: Optional[uuid.UUID] = None
    agent_id: Optional[uuid.UUID] = None
    created_at: datetime
    updated_at: datetime


class GoalAncestryResponse(BaseModel):
    """Full goal ancestry chain from task up to mission."""
    chain: list[GoalResponse] = Field(default_factory=list)
