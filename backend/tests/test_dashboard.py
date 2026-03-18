from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient

from backend.src.models import (
    Phase,
    PhaseStatus,
    Project,
    ProjectStatus,
    Task,
    TaskPriority,
    TaskStatus,
)


@pytest.mark.asyncio
async def test_dashboard_stats_empty(client: AsyncClient) -> None:
    """Empty DB returns zeroes for all stats."""
    resp = await client.get("/api/v1/dashboard/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_projects"] == 0
    assert data["active_projects"] == 0
    assert data["completed_projects"] == 0
    assert data["total_tasks"] == 0
    assert data["active_tasks"] == 0
    assert data["in_progress_tasks"] == 0
    assert data["done_tasks"] == 0
    assert data["completion_rate"] == 0.0


@pytest.mark.asyncio
async def test_dashboard_stats_with_data(client: AsyncClient, db_session) -> None:
    """Stats are computed correctly with projects, tasks, and workers."""
    now = datetime.now(timezone.utc)

    # Create projects: 1 active, 1 completed, 1 design
    for status in (ProjectStatus.active, ProjectStatus.completed, ProjectStatus.design):
        db_session.add(
            Project(
                id=uuid.uuid4(),
                name=f"Project {status.value}",
                description="Test",
                repo_path="/test",
                status=status,
                created_at=now,
                updated_at=now,
            )
        )
    await db_session.flush()

    # Get the active project for tasks
    active_project = (
        await db_session.execute(
            __import__("sqlalchemy").select(Project).where(Project.status == ProjectStatus.active)
        )
    ).scalar_one()

    phase = Phase(
        id=uuid.uuid4(),
        project_id=active_project.id,
        name="Phase 1",
        branch_name="phase-1",
        order=1,
        status=PhaseStatus.active,
        created_at=now,
        updated_at=now,
    )
    db_session.add(phase)
    await db_session.flush()

    # Create tasks: 2 ready, 1 in_progress, 1 done, 1 waiting
    task_statuses = [
        TaskStatus.ready,
        TaskStatus.ready,
        TaskStatus.in_progress,
        TaskStatus.done,
        TaskStatus.waiting,
    ]
    for ts in task_statuses:
        db_session.add(
            Task(
                id=uuid.uuid4(),
                project_id=active_project.id,
                phase_id=phase.id,
                title=f"Task {ts.value}",
                status=ts,
                priority=TaskPriority.medium,
                version=1,
                created_at=now,
                updated_at=now,
            )
        )

    await db_session.commit()

    resp = await client.get("/api/v1/dashboard/stats")
    assert resp.status_code == 200
    data = resp.json()

    # Project counts
    assert data["total_projects"] == 3
    assert data["active_projects"] == 1
    assert data["completed_projects"] == 1

    # Task counts
    assert data["total_tasks"] == 5
    # active = ready(2) + in_progress(1) = 3 (waiting is not "active")
    assert data["active_tasks"] == 3
    assert data["in_progress_tasks"] == 1
    assert data["done_tasks"] == 1
    # completion_rate = 1/5 * 100 = 20.0
    assert data["completion_rate"] == 20.0


@pytest.mark.asyncio
async def test_dashboard_completion_rate_precision(client: AsyncClient, db_session) -> None:
    """Completion rate is rounded to 1 decimal place."""
    now = datetime.now(timezone.utc)

    project = Project(
        id=uuid.uuid4(),
        name="Rate Project",
        description="Test",
        repo_path="/test",
        status=ProjectStatus.active,
        created_at=now,
        updated_at=now,
    )
    db_session.add(project)
    await db_session.flush()

    phase = Phase(
        id=uuid.uuid4(),
        project_id=project.id,
        name="Phase 1",
        branch_name="phase-1",
        order=1,
        status=PhaseStatus.active,
        created_at=now,
        updated_at=now,
    )
    db_session.add(phase)
    await db_session.flush()

    # 1 done out of 3 tasks = 33.3%
    for i, ts in enumerate([TaskStatus.done, TaskStatus.ready, TaskStatus.waiting]):
        db_session.add(
            Task(
                id=uuid.uuid4(),
                project_id=project.id,
                phase_id=phase.id,
                title=f"Task {i}",
                status=ts,
                priority=TaskPriority.medium,
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
    await db_session.commit()

    resp = await client.get("/api/v1/dashboard/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["completion_rate"] == 33.3
