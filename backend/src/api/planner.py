"""Planner approval workflow API — list, approve, reject, modify suggestions and trigger plan generation."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

import structlog
from bsvibe_auth import BSVibeUser
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src import models, schemas
from backend.src.core.auth import Permission, require_permission
from backend.src.core.planner_service import PlannerService
from backend.src.providers.dependencies import get_gateway_provider, get_knowledge_provider
from backend.src.providers.gateway import GatewayProvider
from backend.src.providers.knowledge import KnowledgeProvider
from backend.src.storage.database import get_db

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/planner", tags=["planner"])


# ── Helpers ───────────────────────────────────────────────────────────


async def _get_suggestion_or_404(
    suggestion_id: uuid.UUID,
    db: AsyncSession,
) -> models.TaskSuggestion:
    result = await db.execute(select(models.TaskSuggestion).where(models.TaskSuggestion.id == suggestion_id))
    suggestion = result.scalar_one_or_none()
    if suggestion is None:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    return suggestion


def _ensure_pending(suggestion: models.TaskSuggestion) -> None:
    if suggestion.status != models.SuggestionStatus.pending:
        raise HTTPException(
            status_code=400,
            detail=f"Suggestion is already {suggestion.status.value}, only pending suggestions can be changed",
        )


async def _get_phase_or_404(phase_id: uuid.UUID, db: AsyncSession) -> models.Phase:
    result = await db.execute(select(models.Phase).where(models.Phase.id == phase_id))
    phase = result.scalar_one_or_none()
    if phase is None:
        raise HTTPException(status_code=404, detail="Phase not found")
    return phase


def _task_to_response(task: models.Task) -> schemas.TaskResponse:
    """Build TaskResponse for a newly created task (no dependencies)."""
    return schemas.TaskResponse(
        id=task.id,
        project_id=task.project_id,
        phase_id=task.phase_id,
        title=task.title,
        description=task.description,
        status=schemas.TaskStatus(task.status.value),
        priority=schemas.TaskPriority(task.priority.value),
        task_type=schemas.TaskType(task.task_type.value),
        source=schemas.TaskSource(task.source.value),
        worker_prompt=task.worker_prompt,
        qa_prompt=task.qa_prompt,
        version=task.version,
        created_at=task.created_at,
        updated_at=task.updated_at,
        depends_on=[],
    )


def _create_task_from_suggestion(
    suggestion: models.TaskSuggestion,
    phase: models.Phase,
) -> models.Task:
    """Convert a TaskSuggestion into a Task ORM object."""
    # Map suggestion task_type string to TaskType enum (best-effort)
    try:
        task_type = models.TaskType(suggestion.task_type)
    except ValueError:
        task_type = models.TaskType.feature

    # Map numeric priority to TaskPriority enum
    if suggestion.priority <= 1:
        priority = models.TaskPriority.critical
    elif suggestion.priority <= 3:
        priority = models.TaskPriority.high
    elif suggestion.priority <= 6:
        priority = models.TaskPriority.medium
    else:
        priority = models.TaskPriority.low

    return models.Task(
        project_id=suggestion.project_id,
        phase_id=phase.id,
        title=suggestion.title,
        description=suggestion.description,
        task_type=task_type,
        priority=priority,
        source=models.TaskSource.architect,
        status=models.TaskStatus.ready,
        worker_prompt={"prompt": suggestion.description or suggestion.title},
        qa_prompt={"prompt": f"Verify: {suggestion.title}"},
    )


# ── Endpoints ─────────────────────────────────────────────────────────


@router.get("/briefing", response_model=schemas.BriefingResponse)
async def get_briefing(
    project_id: uuid.UUID = Query(...),
    _auth: BSVibeUser = Depends(require_permission(Permission.planner_read)),
    db: AsyncSession = Depends(get_db),
) -> schemas.BriefingResponse:
    """Return today's morning briefing: suggestions, pending approvals, project status."""
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    # Today's suggestions (all statuses)
    result = await db.execute(
        select(models.TaskSuggestion)
        .where(
            models.TaskSuggestion.project_id == project_id,
            models.TaskSuggestion.created_at >= today_start,
        )
        .order_by(models.TaskSuggestion.priority)
    )
    suggestions = result.scalars().all()

    # Pending count
    pending_result = await db.execute(
        select(func.count())
        .select_from(models.TaskSuggestion)
        .where(
            models.TaskSuggestion.project_id == project_id,
            models.TaskSuggestion.status == models.SuggestionStatus.pending,
        )
    )
    pending_count = pending_result.scalar() or 0

    # Approved today count
    approved_result = await db.execute(
        select(func.count())
        .select_from(models.TaskSuggestion)
        .where(
            models.TaskSuggestion.project_id == project_id,
            models.TaskSuggestion.status == models.SuggestionStatus.approved,
            models.TaskSuggestion.updated_at >= today_start,
        )
    )
    approved_today = approved_result.scalar() or 0

    # Active tasks (not done, not waiting, not redesign)
    active_statuses = [models.TaskStatus.ready, models.TaskStatus.in_progress, models.TaskStatus.review]
    active_result = await db.execute(
        select(func.count())
        .select_from(models.Task)
        .where(
            models.Task.project_id == project_id,
            models.Task.status.in_(active_statuses),
        )
    )
    total_tasks_active = active_result.scalar() or 0

    return schemas.BriefingResponse(
        suggestions=[schemas.TaskSuggestionResponse.model_validate(s) for s in suggestions],
        pending_count=pending_count,
        approved_today=approved_today,
        total_tasks_active=total_tasks_active,
    )


@router.get("/suggestions", response_model=list[schemas.TaskSuggestionResponse])
async def list_suggestions(
    project_id: uuid.UUID = Query(...),
    status: Optional[str] = Query(None),
    _auth: BSVibeUser = Depends(require_permission(Permission.planner_read)),
    db: AsyncSession = Depends(get_db),
) -> list[schemas.TaskSuggestionResponse]:
    """List task suggestions for a project, defaulting to pending status."""
    query = select(models.TaskSuggestion).where(models.TaskSuggestion.project_id == project_id)

    if status is not None:
        try:
            status_enum = models.SuggestionStatus(status)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}") from e
        query = query.where(models.TaskSuggestion.status == status_enum)
    else:
        query = query.where(models.TaskSuggestion.status == models.SuggestionStatus.pending)

    query = query.order_by(models.TaskSuggestion.priority)
    result = await db.execute(query)
    suggestions = result.scalars().all()

    return [schemas.TaskSuggestionResponse.model_validate(s) for s in suggestions]


@router.post("/suggestions/{suggestion_id}/approve", response_model=schemas.TaskResponse)
async def approve_suggestion(
    suggestion_id: uuid.UUID,
    body: schemas.SuggestionApproveRequest,
    _auth: BSVibeUser = Depends(require_permission(Permission.planner_manage)),
    db: AsyncSession = Depends(get_db),
) -> schemas.TaskResponse:
    """Approve a suggestion: mark as approved and create a real Task."""
    suggestion = await _get_suggestion_or_404(suggestion_id, db)
    _ensure_pending(suggestion)
    phase = await _get_phase_or_404(body.phase_id, db)

    # Create task from suggestion
    task = _create_task_from_suggestion(suggestion, phase)
    db.add(task)

    # Update suggestion status
    suggestion.status = models.SuggestionStatus.approved
    await db.commit()
    await db.refresh(task)

    logger.info("suggestion_approved", suggestion_id=str(suggestion_id), task_id=str(task.id))
    return _task_to_response(task)


@router.post("/suggestions/{suggestion_id}/reject", response_model=schemas.TaskSuggestionResponse)
async def reject_suggestion(
    suggestion_id: uuid.UUID,
    body: schemas.SuggestionRejectRequest,
    _auth: BSVibeUser = Depends(require_permission(Permission.planner_manage)),
    db: AsyncSession = Depends(get_db),
) -> schemas.TaskSuggestionResponse:
    """Reject a suggestion with a reason."""
    suggestion = await _get_suggestion_or_404(suggestion_id, db)
    _ensure_pending(suggestion)

    suggestion.status = models.SuggestionStatus.rejected
    suggestion.rejection_reason = body.reason
    await db.commit()
    await db.refresh(suggestion)

    logger.info("suggestion_rejected", suggestion_id=str(suggestion_id), reason=body.reason)
    return schemas.TaskSuggestionResponse.model_validate(suggestion)


@router.post("/suggestions/{suggestion_id}/modify", response_model=schemas.TaskResponse)
async def modify_suggestion(
    suggestion_id: uuid.UUID,
    body: schemas.SuggestionModifyRequest,
    _auth: BSVibeUser = Depends(require_permission(Permission.planner_manage)),
    db: AsyncSession = Depends(get_db),
) -> schemas.TaskResponse:
    """Modify a suggestion's fields, then approve it as a Task."""
    suggestion = await _get_suggestion_or_404(suggestion_id, db)
    _ensure_pending(suggestion)
    phase = await _get_phase_or_404(body.phase_id, db)

    # Apply modifications to suggestion
    update_fields = body.model_dump(exclude_unset=True, exclude={"phase_id"})
    for field, value in update_fields.items():
        setattr(suggestion, field, value)

    # Mark as modified and create task
    suggestion.status = models.SuggestionStatus.modified
    task = _create_task_from_suggestion(suggestion, phase)
    db.add(task)

    await db.commit()
    await db.refresh(task)

    logger.info("suggestion_modified", suggestion_id=str(suggestion_id), task_id=str(task.id))
    return _task_to_response(task)


@router.post("/generate", response_model=list[schemas.TaskSuggestionResponse])
async def generate_plan(
    body: schemas.PlanGenerateRequest,
    _auth: BSVibeUser = Depends(require_permission(Permission.planner_manage)),
    db: AsyncSession = Depends(get_db),
    gateway: GatewayProvider = Depends(get_gateway_provider),
    knowledge: KnowledgeProvider = Depends(get_knowledge_provider),
) -> list[schemas.TaskSuggestionResponse]:
    """Trigger daily plan generation for a project."""
    # Verify project exists
    result = await db.execute(select(models.Project).where(models.Project.id == body.project_id))
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    service = PlannerService(gateway=gateway, knowledge=knowledge)
    suggestions = await service.generate_daily_plan(str(body.project_id), db)

    logger.info("plan_generated", project_id=str(body.project_id), count=len(suggestions))
    return [schemas.TaskSuggestionResponse.model_validate(s) for s in suggestions]
