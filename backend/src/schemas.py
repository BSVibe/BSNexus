import enum
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ── Health Schemas ────────────────────────────────────────────────────


class HealthResponse(BaseModel):
    status: str
    version: str


class DepsHealthResponse(BaseModel):
    redis: str
    postgresql: str


# ── Enums ─────────────────────────────────────────────────────────────


class TaskStatus(str, enum.Enum):
    waiting = "waiting"
    ready = "ready"
    in_progress = "in_progress"
    review = "review"
    done = "done"
    redesign = "redesign"


class TaskPriority(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class ProjectStatus(str, enum.Enum):
    design = "design"
    active = "active"
    paused = "paused"
    completed = "completed"


class PhaseStatus(str, enum.Enum):
    pending = "pending"
    active = "active"
    completed = "completed"


class TaskType(str, enum.Enum):
    feature = "feature"
    bug = "bug"
    improvement = "improvement"
    test = "test"
    chore = "chore"
    refactor = "refactor"


class TaskSource(str, enum.Enum):
    architect = "architect"
    auto_bug = "auto_bug"
    manual = "manual"


# ── Phase Schemas ─────────────────────────────────────────────────────


class PhaseCreate(BaseModel):
    name: str
    description: str
    order: int


class PhaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    description: Optional[str] = None
    branch_name: str
    order: int
    status: PhaseStatus
    created_at: datetime
    updated_at: datetime


# ── Project Schemas ───────────────────────────────────────────────────


class ProjectCreate(BaseModel):
    name: str
    description: str
    repo_path: str


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[ProjectStatus] = None


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str
    design_doc_path: Optional[str] = None
    repo_path: str
    status: ProjectStatus
    llm_config: Optional[dict] = None
    created_at: datetime
    updated_at: datetime
    phases: list[PhaseResponse] = Field(default_factory=list)


# ── Task Schemas ──────────────────────────────────────────────────────


class TaskCreate(BaseModel):
    project_id: uuid.UUID
    phase_id: uuid.UUID
    title: str
    description: str
    priority: TaskPriority
    task_type: TaskType = TaskType.feature
    depends_on: list[uuid.UUID] = Field(default_factory=list)
    worker_prompt: str
    qa_prompt: str


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[TaskPriority] = None
    expected_version: Optional[int] = None


class TaskTransition(BaseModel):
    new_status: TaskStatus
    reason: Optional[str] = None
    actor: str = "user"
    expected_version: Optional[int] = None


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    phase_id: uuid.UUID
    title: str
    description: Optional[str] = None
    status: TaskStatus
    priority: TaskPriority
    task_type: TaskType = TaskType.feature
    source: TaskSource = TaskSource.architect
    parent_task_id: Optional[uuid.UUID] = None
    worker_prompt: Optional[dict] = None
    qa_prompt: Optional[dict] = None
    branch_name: Optional[str] = None
    commit_hash: Optional[str] = None
    qa_result: Optional[dict] = None
    output_path: Optional[str] = None
    error_message: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 3
    qa_feedback_history: Optional[list[dict]] = None
    version: int
    created_at: datetime
    updated_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    depends_on: list[uuid.UUID] = Field(default_factory=list)


# ── Board Schemas ─────────────────────────────────────────────────────


class BoardColumn(BaseModel):
    tasks: list[TaskResponse]


class PhaseInfoResponse(BaseModel):
    name: str
    order: int
    status: PhaseStatus


class BoardResponse(BaseModel):
    project_id: uuid.UUID
    columns: dict[str, BoardColumn]
    stats: dict[str, int]
    phases: dict[str, PhaseInfoResponse] = Field(default_factory=dict)
    redesign_tasks: list[TaskResponse] = Field(default_factory=list)


# ── Common Schemas ────────────────────────────────────────────────────


class SignedPrompt(BaseModel):
    prompt: str
    signature: str
    nonce: str
    timestamp: datetime


class TransitionResponse(BaseModel):
    task_id: uuid.UUID
    status: TaskStatus
    previous_status: TaskStatus
    transition: dict


# ── Architect Schemas ────────────────────────────────────────────────


class DesignSessionStatus(str, enum.Enum):
    active = "active"
    project_bound = "project_bound"
    cancelled = "cancelled"


class MessageRole(str, enum.Enum):
    user = "user"
    assistant = "assistant"


class LLMConfigInput(BaseModel):
    api_key: str
    model: Optional[str] = None
    base_url: Optional[str] = None


class CreateSessionRequest(BaseModel):
    name: Optional[str] = None


class MessageRequest(BaseModel):
    content: str


class DesignMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    session_id: uuid.UUID
    role: MessageRole
    content: str
    created_at: datetime
    finalize_ready: bool = False
    design_context: Optional[str] = None


class DesignSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    project_id: Optional[uuid.UUID] = None
    name: Optional[str] = None
    status: DesignSessionStatus
    created_at: datetime
    updated_at: datetime
    messages: list[DesignMessageResponse] = Field(default_factory=list)


class FinalizeRequest(BaseModel):
    repo_path: str
    pm_llm_config: Optional[LLMConfigInput] = None


class PhaseRedesignRequest(BaseModel):
    """Request to trigger manual phase-level redesign."""

    llm_config: Optional[LLMConfigInput] = None


class PhaseRedesignResponse(BaseModel):
    """Response from phase-level redesign."""

    phase_id: uuid.UUID
    project_id: uuid.UUID
    reasoning: str
    tasks_kept: int
    tasks_deleted: int
    tasks_created: int


class AddTaskRequest(BaseModel):
    phase_id: uuid.UUID
    request_text: str
    llm_config: Optional[LLMConfigInput] = None


class AddTaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    title: str
    description: Optional[str] = None
    priority: TaskPriority
    worker_prompt: Optional[dict] = None
    qa_prompt: Optional[dict] = None


# ── Dashboard Schemas ────────────────────────────────────────────────


class DashboardStatsResponse(BaseModel):
    total_projects: int
    active_projects: int
    completed_projects: int
    total_tasks: int
    active_tasks: int
    in_progress_tasks: int
    done_tasks: int
    completion_rate: float


class ProjectDashboardSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    status: ProjectStatus
    task_counts: dict[str, int]  # status → count
    bug_count: int = 0
    current_phase: Optional[str] = None
    has_architect_session: bool = False
    last_activity: Optional[datetime] = None


# ── Settings Schemas ─────────────────────────────────────────────────


class GlobalSettingsResponse(BaseModel):
    llm_api_key: Optional[str] = None
    llm_model: Optional[str] = None
    llm_base_url: Optional[str] = None


class GlobalSettingsUpdate(BaseModel):
    llm_api_key: Optional[str] = None
    llm_model: Optional[str] = None
    llm_base_url: Optional[str] = None


# ── Batch Delete Schemas ────────────────────────────────────────────


class BatchDeleteRequest(BaseModel):
    ids: list[uuid.UUID]


class BatchDeleteResponse(BaseModel):
    deleted: int


class DeleteResponse(BaseModel):
    detail: str


# ── Security Schemas ───────────────────────────────────────────────


class SecurityFindingResponse(BaseModel):
    category: str
    severity: str
    title: str
    description: str
    recommendation: str
    affected_component: Optional[str] = None


class SecurityReportResponse(BaseModel):
    scan_timestamp: datetime
    passed: bool
    summary: dict[str, int]
    findings: list[SecurityFindingResponse]


class AuditLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    timestamp: datetime
    action: str
    severity: str
    actor_id: Optional[str] = None
    actor_type: Optional[str] = None
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    ip_address: Optional[str] = None
    details: Optional[dict] = None
    request_path: Optional[str] = None
    request_method: Optional[str] = None


class AuditLogListResponse(BaseModel):
    total: int
    items: list[AuditLogResponse]


class ComplianceReportResponse(BaseModel):
    generated_at: str
    frameworks: list[str]
    overall_status: str
    summary: dict[str, int]
    checks: list[dict]


class APIKeyCreateRequest(BaseModel):
    name: str
    role: str = "viewer"
    expires_in_days: Optional[int] = None


class APIKeyCreateResponse(BaseModel):
    id: uuid.UUID
    name: str
    key: str
    role: str
    created_at: datetime
    expires_at: Optional[datetime] = None


class APIKeyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    key_prefix: str
    role: str
    is_active: bool
    created_at: datetime
    expires_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
