"""Plan proposals API — list, approve, reject pending Phase/Task proposals."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from bsvibe_auth import BSVibeUser
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src import models
from backend.src.core.auth import Permission, require_permission
from backend.src.core.harness import read_approval_settings
from backend.src.core.tenant_context import get_tenant_id
from backend.src.repositories.phase_repository import PhaseRepository
from backend.src.storage.database import get_db

router = APIRouter(prefix="/api/v1/projects/{project_id}/proposals", tags=["plan-proposals"])


class ProposalResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    proposer_agent_name: str | None
    proposal_type: str
    payload: dict[str, Any]
    status: str
    created_at: datetime


class ApprovalSettingsResponse(BaseModel):
    phase_creation: str
    task_creation: str


class ApprovalSettingsUpdate(BaseModel):
    phase_creation: str | None = None
    task_creation: str | None = None


@router.get("", response_model=list[ProposalResponse])
async def list_proposals(
    project_id: uuid.UUID,
    status: str = "pending",
    _auth: BSVibeUser = Depends(require_permission(Permission.plan_read)),
    db: AsyncSession = Depends(get_db),
) -> list[ProposalResponse]:
    result = await db.execute(
        select(models.PlanProposal)
        .where(
            models.PlanProposal.project_id == project_id,
            models.PlanProposal.status == status,
        )
        .order_by(models.PlanProposal.created_at.asc())
    )
    return [
        ProposalResponse(
            id=p.id, project_id=p.project_id,
            proposer_agent_name=p.proposer_agent_name,
            proposal_type=p.proposal_type.value if hasattr(p.proposal_type, 'value') else p.proposal_type,
            payload=p.payload, status=p.status.value if hasattr(p.status, 'value') else p.status,
            created_at=p.created_at,
        )
        for p in result.scalars().all()
    ]


@router.post("/{proposal_id}/approve", response_model=dict)
async def approve_proposal(
    project_id: uuid.UUID,
    proposal_id: uuid.UUID,
    _auth: BSVibeUser = Depends(require_permission(Permission.plan_read)),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> dict:
    """Approve a proposal — creates the actual Phase or Task."""
    result = await db.execute(
        select(models.PlanProposal).where(
            models.PlanProposal.id == proposal_id,
            models.PlanProposal.project_id == project_id,
        )
    )
    proposal = result.scalar_one_or_none()
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if proposal.status != models.ProposalStatus.pending:
        raise HTTPException(status_code=400, detail=f"Proposal already {proposal.status}")

    data = proposal.payload
    created_id: str = ""

    if proposal.proposal_type == models.ProposalType.phase:
        name = data.get("name", "Untitled Phase")
        phase_repo = PhaseRepository(db)
        existing = await phase_repo.list_by_project(project_id)
        next_order = max((p.order for p in existing), default=0) + 1
        description = data.get("description", "")
        branch = data.get("branch_name", f"phase/{name.lower().replace(' ', '-')}")
        phase = models.Phase(
            project_id=project_id, name=name, description=description,
            branch_name=branch, order=next_order,
            status=models.PhaseStatus.pending,
        )
        db.add(phase)
        await db.flush()
        created_id = str(phase.id)

    elif proposal.proposal_type == models.ProposalType.task:
        title = data.get("title", "Untitled Task")
        # Find or create active phase.
        phase_repo = PhaseRepository(db)
        phases = await phase_repo.list_by_project(project_id)
        active = next((p for p in phases if p.status == models.PhaseStatus.active), None)
        if not active and phases:
            active = phases[0]
        if not active:
            active = models.Phase(
                project_id=project_id, name="Phase 1",
                description="Auto-created", branch_name="phase/phase-1",
                order=1, status=models.PhaseStatus.active,
            )
            db.add(active)
            await db.flush()

        try:
            priority = models.TaskPriority(data.get("priority", "medium"))
        except ValueError:
            priority = models.TaskPriority.medium
        try:
            task_type = models.TaskType(data.get("task_type", "feature"))
        except ValueError:
            task_type = models.TaskType.feature

        task = models.Task(
            project_id=project_id, phase_id=active.id,
            title=title, description=data.get("description"),
            priority=priority, task_type=task_type,
            source=models.TaskSource.llm, status=models.TaskStatus.pending,
            agent_id=proposal.proposer_agent_id,
            worker_prompt={"prompt": data.get("worker_prompt", "")},
            qa_prompt={"prompt": data.get("qa_prompt", "")},
            branch_name=active.branch_name,
        )
        db.add(task)
        await db.flush()
        created_id = str(task.id)

    proposal.status = models.ProposalStatus.approved
    proposal.resolved_at = datetime.now(timezone.utc)
    await db.commit()

    return {"status": "approved", "created_id": created_id}


@router.post("/{proposal_id}/reject", response_model=dict)
async def reject_proposal(
    project_id: uuid.UUID,
    proposal_id: uuid.UUID,
    _auth: BSVibeUser = Depends(require_permission(Permission.plan_read)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(models.PlanProposal).where(
            models.PlanProposal.id == proposal_id,
            models.PlanProposal.project_id == project_id,
        )
    )
    proposal = result.scalar_one_or_none()
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if proposal.status != models.ProposalStatus.pending:
        raise HTTPException(status_code=400, detail=f"Proposal already {proposal.status}")
    proposal.status = models.ProposalStatus.rejected
    proposal.resolved_at = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "rejected"}


# ── Approval settings ───────────────────────────────────────────────


@router.get("/settings", response_model=ApprovalSettingsResponse)
async def get_approval_settings(
    project_id: uuid.UUID,
    _auth: BSVibeUser = Depends(require_permission(Permission.plan_read)),
    db: AsyncSession = Depends(get_db),
) -> ApprovalSettingsResponse:
    project_result = await db.execute(
        select(models.Project).where(models.Project.id == project_id)
    )
    project = project_result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    settings = read_approval_settings(project.workspace_dir)
    return ApprovalSettingsResponse(**settings)


@router.put("/settings", response_model=ApprovalSettingsResponse)
async def update_approval_settings(
    project_id: uuid.UUID,
    body: ApprovalSettingsUpdate,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_update)),
    db: AsyncSession = Depends(get_db),
) -> ApprovalSettingsResponse:
    from backend.src.core.harness import write_approval_settings

    project_result = await db.execute(
        select(models.Project).where(models.Project.id == project_id)
    )
    project = project_result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    current = read_approval_settings(project.workspace_dir)
    valid = {"auto_approve", "require_approval"}
    if body.phase_creation and body.phase_creation in valid:
        current["phase_creation"] = body.phase_creation
    if body.task_creation and body.task_creation in valid:
        current["task_creation"] = body.task_creation

    write_approval_settings(project.workspace_dir, current)
    return ApprovalSettingsResponse(**current)
