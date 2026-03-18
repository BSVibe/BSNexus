from __future__ import annotations

import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.src import models, schemas
from backend.src.storage.database import get_db

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


@router.get("/stats", response_model=schemas.DashboardStatsResponse)
async def get_dashboard_stats(db: AsyncSession = Depends(get_db)) -> schemas.DashboardStatsResponse:
    """Return aggregated dashboard statistics for projects, tasks, and workers."""
    # Project stats
    project_result = await db.execute(select(models.Project))
    projects = project_result.scalars().all()
    total_projects = len(projects)
    active_projects = sum(1 for p in projects if p.status == models.ProjectStatus.active)
    completed_projects = sum(1 for p in projects if p.status == models.ProjectStatus.completed)

    # Task stats
    task_result = await db.execute(select(models.Task))
    tasks = task_result.scalars().all()
    total_tasks = len(tasks)
    active_tasks = sum(
        1
        for t in tasks
        if t.status in (models.TaskStatus.ready, models.TaskStatus.queued, models.TaskStatus.in_progress, models.TaskStatus.review)
    )
    in_progress_tasks = sum(1 for t in tasks if t.status == models.TaskStatus.in_progress)
    done_tasks = sum(1 for t in tasks if t.status == models.TaskStatus.done)
    completion_rate = round((done_tasks / total_tasks * 100), 1) if total_tasks > 0 else 0.0

    return schemas.DashboardStatsResponse(
        total_projects=total_projects,
        active_projects=active_projects,
        completed_projects=completed_projects,
        total_tasks=total_tasks,
        active_tasks=active_tasks,
        in_progress_tasks=in_progress_tasks,
        done_tasks=done_tasks,
        completion_rate=completion_rate,
    )


@router.get("/projects-summary", response_model=list[schemas.ProjectDashboardSummary])
async def get_projects_summary(
    db: AsyncSession = Depends(get_db),
) -> list[schemas.ProjectDashboardSummary]:
    """Return per-project dashboard summaries with task breakdown."""
    # Load projects with phases
    project_result = await db.execute(
        select(models.Project).options(selectinload(models.Project.phases))
    )
    projects = project_result.scalars().all()

    if not projects:
        return []

    project_ids = [p.id for p in projects]

    # Batch query: task counts by project and status
    task_counts_result = await db.execute(
        select(
            models.Task.project_id,
            models.Task.status,
            func.count(models.Task.id),
        )
        .where(models.Task.project_id.in_(project_ids))
        .group_by(models.Task.project_id, models.Task.status)
    )
    task_counts: dict[uuid.UUID, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for project_id, status, count in task_counts_result:
        task_counts[project_id][status.value] = count

    # Batch query: bug counts by project
    bug_counts_result = await db.execute(
        select(
            models.Task.project_id,
            func.count(models.Task.id),
        )
        .where(
            models.Task.project_id.in_(project_ids),
            models.Task.task_type == models.TaskType.bug,
        )
        .group_by(models.Task.project_id)
    )
    bug_counts: dict[uuid.UUID, int] = {}
    for project_id, count in bug_counts_result:
        bug_counts[project_id] = count

    # Check for architect sessions
    session_result = await db.execute(
        select(models.DesignSession.project_id)
        .where(
            models.DesignSession.project_id.in_(project_ids),
            models.DesignSession.status == models.DesignSessionStatus.project_bound,
        )
    )
    projects_with_sessions: set[uuid.UUID] = {row[0] for row in session_result if row[0]}

    # Batch query: last activity (latest task updated_at per project)
    activity_result = await db.execute(
        select(
            models.Task.project_id,
            func.max(models.Task.updated_at),
        )
        .where(models.Task.project_id.in_(project_ids))
        .group_by(models.Task.project_id)
    )
    last_activities: dict[uuid.UUID, object] = {}
    for project_id, last_updated in activity_result:
        last_activities[project_id] = last_updated

    # Build summaries
    summaries = []
    for project in projects:
        active_phase = next(
            (p for p in project.phases if p.status == models.PhaseStatus.active), None
        )
        summaries.append(
            schemas.ProjectDashboardSummary(
                id=project.id,
                name=project.name,
                status=schemas.ProjectStatus(project.status.value),
                task_counts=dict(task_counts.get(project.id, {})),
                bug_count=bug_counts.get(project.id, 0),
                current_phase=active_phase.name if active_phase else None,
                has_architect_session=project.id in projects_with_sessions,
                last_activity=last_activities.get(project.id),
            )
        )

    return summaries
