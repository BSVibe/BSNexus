from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient

from backend.src.api.dashboard import get_dashboard_stats
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
        await db_session.execute(__import__("sqlalchemy").select(Project).where(Project.status == ProjectStatus.active))
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
        TaskStatus.pending,
        TaskStatus.pending,
        TaskStatus.running,
        TaskStatus.done,
        TaskStatus.pending,
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
    # active = pending(3) + running(1) = 4
    assert data["active_tasks"] == 4
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
    for i, ts in enumerate([TaskStatus.done, TaskStatus.pending, TaskStatus.pending]):
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


# ── Direct-call tests (bypass ASGITransport for coverage tracking) ──────────


async def test_get_dashboard_stats_direct_empty(db_session) -> None:
    """Direct call: empty DB returns zeroes."""
    result = await get_dashboard_stats(db=db_session)
    assert result.total_projects == 0
    assert result.active_projects == 0
    assert result.completed_projects == 0
    assert result.total_tasks == 0
    assert result.active_tasks == 0
    assert result.in_progress_tasks == 0
    assert result.done_tasks == 0
    assert result.completion_rate == 0.0


async def test_get_dashboard_stats_direct_with_data(db_session) -> None:
    """Direct call: stats computed correctly with various task statuses."""
    now = datetime.now(timezone.utc)

    for status in (ProjectStatus.active, ProjectStatus.completed, ProjectStatus.design):
        db_session.add(
            Project(
                id=uuid.uuid4(),
                name=f"P-{status.value}",
                description="d",
                repo_path="/t",
                status=status,
                created_at=now,
                updated_at=now,
            )
        )
    await db_session.flush()

    from sqlalchemy import select

    active_project = (
        await db_session.execute(select(Project).where(Project.status == ProjectStatus.active))
    ).scalar_one()

    phase = Phase(
        id=uuid.uuid4(),
        project_id=active_project.id,
        name="Ph1",
        branch_name="b",
        order=1,
        status=PhaseStatus.active,
        created_at=now,
        updated_at=now,
    )
    db_session.add(phase)
    await db_session.flush()

    # 3 pending + 2 running + 2 done = 7 total
    for ts in [
        TaskStatus.pending,
        TaskStatus.pending,
        TaskStatus.pending,
        TaskStatus.running,
        TaskStatus.running,
        TaskStatus.done,
        TaskStatus.done,
    ]:
        db_session.add(
            Task(
                id=uuid.uuid4(),
                project_id=active_project.id,
                phase_id=phase.id,
                title=f"Task-{ts.value}",
                status=ts,
                priority=TaskPriority.medium,
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
    await db_session.flush()

    result = await get_dashboard_stats(db=db_session)

    assert result.total_projects == 3
    assert result.active_projects == 1
    assert result.completed_projects == 1
    assert result.total_tasks == 7
    # active = pending(3) + running(2) = 5
    assert result.active_tasks == 5
    assert result.in_progress_tasks == 2
    assert result.done_tasks == 2
    # completion_rate = 2/7 * 100 = 28.6
    assert result.completion_rate == 28.6


async def test_get_dashboard_stats_direct_all_done(db_session) -> None:
    """Direct call: 100% completion rate when all tasks are done."""
    now = datetime.now(timezone.utc)

    project = Project(
        id=uuid.uuid4(),
        name="AllDone",
        description="d",
        repo_path="/t",
        status=ProjectStatus.completed,
        created_at=now,
        updated_at=now,
    )
    db_session.add(project)
    await db_session.flush()

    phase = Phase(
        id=uuid.uuid4(),
        project_id=project.id,
        name="Ph1",
        branch_name="b",
        order=1,
        status=PhaseStatus.completed,
        created_at=now,
        updated_at=now,
    )
    db_session.add(phase)
    await db_session.flush()

    for i in range(4):
        db_session.add(
            Task(
                id=uuid.uuid4(),
                project_id=project.id,
                phase_id=phase.id,
                title=f"Done-{i}",
                status=TaskStatus.done,
                priority=TaskPriority.low,
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
    await db_session.flush()

    result = await get_dashboard_stats(db=db_session)

    assert result.total_tasks == 4
    assert result.active_tasks == 0
    assert result.done_tasks == 4
    assert result.completion_rate == 100.0
    assert result.completed_projects == 1


async def test_dashboard_stats_pending_and_running_counted_as_active(
    client: AsyncClient, db_session
) -> None:
    """pending + running tasks are counted as active_tasks; blocked/done are not."""
    now = datetime.now(timezone.utc)

    project = Project(
        id=uuid.uuid4(),
        name="Active Project",
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

    # pending(2) + running(1) = 3 active; blocked(1) + done(1) not active
    for ts in [
        TaskStatus.pending,
        TaskStatus.pending,
        TaskStatus.running,
        TaskStatus.blocked,
        TaskStatus.done,
    ]:
        db_session.add(
            Task(
                id=uuid.uuid4(),
                project_id=project.id,
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

    assert data["total_tasks"] == 5
    assert data["active_tasks"] == 3
    assert data["in_progress_tasks"] == 1
    assert data["done_tasks"] == 1
    assert data["completion_rate"] == 20.0


async def test_dashboard_stats_all_done(client: AsyncClient, db_session) -> None:
    """When all tasks are done, completion_rate is 100.0 and active_tasks is 0."""
    now = datetime.now(timezone.utc)

    project = Project(
        id=uuid.uuid4(),
        name="Done Project",
        description="Test",
        repo_path="/test",
        status=ProjectStatus.completed,
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
        status=PhaseStatus.completed,
        created_at=now,
        updated_at=now,
    )
    db_session.add(phase)
    await db_session.flush()

    for i in range(3):
        db_session.add(
            Task(
                id=uuid.uuid4(),
                project_id=project.id,
                phase_id=phase.id,
                title=f"Done Task {i}",
                status=TaskStatus.done,
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

    assert data["total_tasks"] == 3
    assert data["active_tasks"] == 0
    assert data["done_tasks"] == 3
    assert data["completion_rate"] == 100.0
    assert data["completed_projects"] == 1
