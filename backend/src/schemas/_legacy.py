import enum
import ipaddress
import re
import socket
import uuid
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ── Validation helpers ───────────────────────────────────────────────

_PATH_TRAVERSAL_RE = re.compile(r"\.\.[/\\]|%2e%2e[/\\]|%252e%252e[/\\]", re.IGNORECASE)

_SSRF_BLOCKED_HOSTNAMES = frozenset({"localhost"})


def _check_path_traversal(v: str) -> str:
    """Reject paths containing directory traversal sequences."""
    if _PATH_TRAVERSAL_RE.search(v):
        raise ValueError("Path traversal sequences are not allowed")
    return v


def _is_private_ip(host: str) -> bool:
    """Check if a hostname resolves to a private/loopback/link-local IP.

    Handles all IP notations: dotted decimal, shorthand (127.1),
    hex (0x7f000001), octal (0177.0.0.1), IPv6 mapped (::ffff:127.0.0.1).
    """
    try:
        # socket.getaddrinfo handles all notation variants
        results = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for _, _, _, _, sockaddr in results:
            addr = ipaddress.ip_address(sockaddr[0])
            if addr.is_loopback or addr.is_private or addr.is_link_local or addr.is_reserved:
                return True
    except (socket.gaierror, ValueError, OSError):
        pass
    return False


def _check_ssrf_url(v: str | None) -> str | None:
    """Reject URLs targeting private/internal networks."""
    if v is None:
        return v
    try:
        parsed = urlparse(v)
    except ValueError as e:
        raise ValueError(f"Invalid URL: {e}") from e
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("URL must contain a hostname")
    if host in _SSRF_BLOCKED_HOSTNAMES:
        raise ValueError("URLs targeting localhost are not allowed")
    if _is_private_ip(host):
        raise ValueError("URLs targeting private or internal network addresses are not allowed")
    return v


# ── Health Schemas ────────────────────────────────────────────────────


class HealthResponse(BaseModel):
    status: str
    version: str


class DepsHealthResponse(BaseModel):
    redis: str
    postgresql: str


# ── Enums ─────────────────────────────────────────────────────────────


class TaskStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    blocked = "blocked"
    done = "done"


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
    llm = "llm"
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
    description: str = ""
    repo_path: Optional[str] = None
    workspace_type: str = "server_managed"  # server_managed | local_import
    github_repo_url: Optional[str] = None
    github_branch: str = "main"

    @field_validator("repo_path")
    @classmethod
    def _validate_repo_path(cls, v: str | None) -> str | None:
        if not v:
            return v
        return _check_path_traversal(v)


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
    repo_path: Optional[str] = None
    workspace_type: str = "server_managed"
    workspace_dir: Optional[str] = None
    github_repo_url: Optional[str] = None
    github_branch: Optional[str] = None
    status: ProjectStatus
    llm_config: Optional[dict] = None
    created_at: datetime
    updated_at: datetime
    phases: list[PhaseResponse] = Field(default_factory=list)

    @model_validator(mode="after")
    def _mask_llm_config_keys(self) -> "ProjectResponse":
        """Mask API keys in llm_config to prevent credential leakage in responses."""
        if not self.llm_config:
            return self
        masked = {}
        for role, cfg in self.llm_config.items():
            if isinstance(cfg, dict) and "api_key" in cfg:
                cfg = {**cfg, "api_key": _mask_api_key(cfg["api_key"])}
            masked[role] = cfg
        self.llm_config = masked
        return self


def _mask_api_key(key: str | None) -> str | None:
    """Mask API key for display: sk-ant-abc...xyz -> sk-****...xyz"""
    if not key or len(key) < 8:
        return key
    return key[:3] + "****..." + key[-4:]


# ── Task Schemas ──────────────────────────────────────────────────────


class TaskCreate(BaseModel):
    project_id: uuid.UUID
    phase_id: uuid.UUID
    title: str
    description: str = ""
    priority: TaskPriority = TaskPriority.medium
    task_type: TaskType = TaskType.feature
    creator_agent_id: Optional[uuid.UUID] = None
    executor_type: str = "coding"
    executor_metadata: dict = Field(default_factory=dict)
    depends_on: list[uuid.UUID] = Field(default_factory=list)
    worker_prompt: str = ""
    qa_prompt: str = ""


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[TaskPriority] = None
    creator_agent_id: Optional[uuid.UUID] = None
    executor_type: Optional[str] = None
    executor_metadata: Optional[dict] = None
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
    executor_type: str = "coding"
    executor_metadata: dict = Field(default_factory=dict)
    source: TaskSource = TaskSource.llm
    creator_agent_id: Optional[uuid.UUID] = None
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


# ── TaskSuggestion Schemas ────────────────────────────────────────────


class SuggestionStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    modified = "modified"


class TaskSuggestionCreate(BaseModel):
    project_id: uuid.UUID
    title: str
    description: Optional[str] = None
    task_type: str
    priority: int = Field(..., gt=0)
    estimated_effort: Optional[str] = None
    reasoning: Optional[str] = None


class TaskSuggestionUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    task_type: Optional[str] = None
    priority: Optional[int] = Field(default=None, gt=0)
    estimated_effort: Optional[str] = None
    reasoning: Optional[str] = None
    status: Optional[SuggestionStatus] = None
    rejection_reason: Optional[str] = None


class TaskSuggestionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    description: Optional[str] = None
    task_type: str
    priority: int
    estimated_effort: Optional[str] = None
    reasoning: Optional[str] = None
    status: SuggestionStatus
    rejection_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class SuggestionApproveRequest(BaseModel):
    phase_id: uuid.UUID


class SuggestionRejectRequest(BaseModel):
    reason: str = Field(..., min_length=1)


class SuggestionModifyRequest(BaseModel):
    phase_id: uuid.UUID
    title: Optional[str] = None
    description: Optional[str] = None
    task_type: Optional[str] = None
    priority: Optional[int] = Field(default=None, gt=0)
    estimated_effort: Optional[str] = None
    reasoning: Optional[str] = None


class PlanGenerateRequest(BaseModel):
    project_id: uuid.UUID


class BriefingResponse(BaseModel):
    suggestions: list[TaskSuggestionResponse]
    pending_count: int
    approved_today: int
    total_tasks_active: int


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
    last_activity: Optional[datetime] = None


# ── Settings Schemas ─────────────────────────────────────────────────


class GlobalSettingsResponse(BaseModel):
    llm_api_key: Optional[str] = None
    llm_model: Optional[str] = None
    llm_base_url: Optional[str] = None
    default_executor_type: str = "generic_llm"


class GlobalSettingsUpdate(BaseModel):
    llm_api_key: Optional[str] = None
    llm_model: Optional[str] = None
    llm_base_url: Optional[str] = None
    default_executor_type: Optional[str] = None

    @field_validator("llm_base_url")
    @classmethod
    def _validate_base_url(cls, v: str | None) -> str | None:
        return _check_ssrf_url(v)


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
