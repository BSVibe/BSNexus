"""Agent Pydantic schemas for request/response."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _normalize_agent_name(name: str) -> str:
    """Normalize agent name: trim and replace spaces with underscores for clean @mentions."""
    cleaned = name.strip().replace(" ", "_")
    if not cleaned:
        raise ValueError("Agent name cannot be empty")
    return cleaned


class AgentCreate(BaseModel):
    name: str
    role: str
    title: Optional[str] = None
    job_description: Optional[str] = None
    executor_config_id: Optional[uuid.UUID] = None
    executor_type: str = "generic_llm"
    executor_config: dict = Field(default_factory=dict)
    system_prompt: Optional[str] = None
    skills: Optional[list[str]] = None
    capabilities: list[str] = Field(default_factory=lambda: ["general"])
    parent_agent_id: Optional[uuid.UUID] = None
    heartbeat_interval_seconds: Optional[int] = None
    heartbeat_enabled: bool = False
    monthly_budget_cents: Optional[int] = None

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, v: str) -> str:
        return _normalize_agent_name(v)


class AgentUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    title: Optional[str] = None
    job_description: Optional[str] = None
    executor_config_id: Optional[uuid.UUID] = None
    executor_type: Optional[str] = None
    executor_config: Optional[dict] = None
    system_prompt: Optional[str] = None
    skills: Optional[list[str]] = None
    capabilities: Optional[list[str]] = None
    parent_agent_id: Optional[uuid.UUID] = None
    heartbeat_interval_seconds: Optional[int] = None
    heartbeat_enabled: Optional[bool] = None
    monthly_budget_cents: Optional[int] = None
    is_active: Optional[bool] = None

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, v: Optional[str]) -> Optional[str]:
        return _normalize_agent_name(v) if v is not None else None


class CurrentTaskBrief(BaseModel):
    """Minimal info about the task an agent is currently working on."""

    id: uuid.UUID
    title: str
    status: str


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
    parent_agent_id: Optional[uuid.UUID] = None
    heartbeat_interval_seconds: Optional[int] = None
    heartbeat_enabled: bool = False
    last_heartbeat_at: Optional[datetime] = None
    monthly_budget_cents: Optional[int] = None
    current_month_spent_cents: int = 0
    status: str = "offline"
    # Plan-view compatible status dot (green/yellow/red/gray).
    # Populated at read-time by the agents API so all consumers share
    # a single source of truth — previously the Plan view and the
    # Agents tab had separate resolvers that disagreed.
    dot: str = "gray"
    current_task: Optional[CurrentTaskBrief] = None
    # Short human-readable activity summary when the agent is mid-chat
    # (e.g. "시장 조사 중...", "코드 리뷰 진행 중..."). Empty when idle.
    activity: str = ""
    is_active: bool = True
    created_at: datetime
    updated_at: datetime


class AgentOrgChartResponse(BaseModel):
    """Agent with children for org chart tree rendering."""

    agent: AgentResponse
    children: list["AgentOrgChartResponse"] = Field(default_factory=list)
