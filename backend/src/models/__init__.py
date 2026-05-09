from bsvibe_audit import register_audit_outbox_with

from backend.src.core.domain import (
    BriefScope,
    DeliverableStatus,
    DeliverableType,
    DirectionSource,
    ProofAttemptStatus,
    ProofState,
    RequestStatus,
    RunAttemptPhase,
    RunAttemptStatus,
    WorkPlanCreatedBy,
    WorkPlanStatus,
    WorkStepStatus,
)
from backend.src.models.brief_snapshot import BriefSnapshot
from backend.src.models.decision import Decision
from backend.src.models.deliverable import Deliverable
from backend.src.models.direction import Direction
from backend.src.models.project import Project, ProjectStatus, WorkspaceType
from backend.src.models.proof import ProofAttempt, ProofPolicy
from backend.src.models.request import Request
from backend.src.models.run_attempt import RunAttempt, ToolEvent
from backend.src.models.tenant import Tenant, TenantMember
from backend.src.models.work_plan import WorkPlan
from backend.src.models.work_step import WorkStep
from backend.src.storage.database import Base

register_audit_outbox_with(Base.metadata)

__all__ = [
    "BriefScope",
    "BriefSnapshot",
    "Decision",
    "Deliverable",
    "DeliverableStatus",
    "DeliverableType",
    "Direction",
    "DirectionSource",
    "ProofAttempt",
    "ProofAttemptStatus",
    "ProofPolicy",
    "ProofState",
    "Project",
    "ProjectStatus",
    "Request",
    "RequestStatus",
    "RunAttempt",
    "RunAttemptPhase",
    "RunAttemptStatus",
    "Tenant",
    "TenantMember",
    "ToolEvent",
    "WorkPlan",
    "WorkPlanCreatedBy",
    "WorkPlanStatus",
    "WorkStep",
    "WorkStepStatus",
    "WorkspaceType",
]
