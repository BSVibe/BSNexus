from __future__ import annotations

import enum


class DirectionSource(str, enum.Enum):
    web = "web"
    mobile_web = "mobile_web"
    slack = "slack"
    email = "email"
    cli = "cli"
    voice = "voice"


class RequestStatus(str, enum.Enum):
    open = "open"
    running = "running"
    blocked = "blocked"
    review_ready = "review_ready"
    shipped = "shipped"
    abandoned = "abandoned"


class WorkPlanStatus(str, enum.Enum):
    draft = "draft"
    active = "active"
    superseded = "superseded"
    completed = "completed"


class WorkPlanCreatedBy(str, enum.Enum):
    system = "system"
    llm_assisted = "llm_assisted"
    user = "user"


class WorkStepStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    needs_decision = "needs_decision"
    verifying = "verifying"
    review_ready = "review_ready"
    failed = "failed"
    skipped = "skipped"


class RunAttemptPhase(str, enum.Enum):
    prepare = "prepare"
    work = "work"
    verify = "verify"
    summarize = "summarize"
    terminal = "terminal"


class RunAttemptStatus(str, enum.Enum):
    running = "running"
    completed = "completed"
    failed = "failed"
    timed_out = "timed_out"


class DeliverableType(str, enum.Enum):
    code = "code"
    pr = "pr"
    preview = "preview"
    design = "design"
    doc = "doc"
    data = "data"
    marketing = "marketing"
    report = "report"


class DeliverableStatus(str, enum.Enum):
    draft = "draft"
    verifying = "verifying"
    review_ready = "review_ready"
    shipped = "shipped"
    rejected = "rejected"


class ProofState(str, enum.Enum):
    verification_missing = "verification_missing"
    verifying = "verifying"
    verified = "verified"
    verification_failed = "verification_failed"
    human_review_required = "human_review_required"


class ProofAttemptStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    verified = "verified"
    failed = "failed"
    human_review_required = "human_review_required"


class BriefScope(str, enum.Enum):
    company = "company"
    project = "project"
    request = "request"
