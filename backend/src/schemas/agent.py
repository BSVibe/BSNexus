"""Agent Pydantic schemas for request/response."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class AgentCreate(BaseModel):
    name: str
    role: str
    title: Optional[str] = None
    job_description: Optional[str] = None
    executor_config_id: Optional[uuid.UUID] = None  # NULL = use tenant default
    executor_type: str = "claude_api"
    executor_config: dict = Field(default_factory=dict)
    system_prompt: Optional[str] = None
    skills: Optional[list[str]] = None
    capabilities: list[str] = Field(default_factory=lambda: ["general"])
    routing_keywords: list[str] = Field(default_factory=list)
    parent_agent_id: Optional[uuid.UUID] = None
    heartbeat_interval_seconds: Optional[int] = None
    heartbeat_enabled: bool = False
    monthly_budget_cents: Optional[int] = None


class AgentUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    title: Optional[str] = None
    job_description: Optional[str] = None
    executor_config_id: Optional[uuid.UUID] = None  # NULL = use tenant default
    executor_type: Optional[str] = None
    executor_config: Optional[dict] = None
    system_prompt: Optional[str] = None
    skills: Optional[list[str]] = None
    capabilities: Optional[list[str]] = None
    routing_keywords: Optional[list[str]] = None
    parent_agent_id: Optional[uuid.UUID] = None
    heartbeat_interval_seconds: Optional[int] = None
    heartbeat_enabled: Optional[bool] = None
    monthly_budget_cents: Optional[int] = None
    is_active: Optional[bool] = None


class AgentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    role: str
    title: Optional[str] = None
    job_description: Optional[str] = None
    executor_config_id: Optional[uuid.UUID] = None
    executor_type: str
    executor_config: dict = Field(default_factory=dict)
    system_prompt: Optional[str] = None
    skills: Optional[list[str]] = None
    capabilities: list[str] = Field(default_factory=list)
    routing_keywords: list[str] = Field(default_factory=list)
    parent_agent_id: Optional[uuid.UUID] = None
    heartbeat_interval_seconds: Optional[int] = None
    heartbeat_enabled: bool = False
    last_heartbeat_at: Optional[datetime] = None
    monthly_budget_cents: Optional[int] = None
    current_month_spent_cents: int = 0
    status: str = "offline"
    is_active: bool = True
    created_at: datetime
    updated_at: datetime


class AgentOrgChartResponse(BaseModel):
    """Agent with children for org chart tree rendering."""

    agent: AgentResponse
    children: list["AgentOrgChartResponse"] = Field(default_factory=list)
