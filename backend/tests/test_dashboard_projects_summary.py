"""Tests for GET /api/v1/dashboard/projects-summary endpoint."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient

from backend.src.models import (
    DesignSession,
    DesignSessionStatus,
    Phase,
    PhaseStatus,
    Project,
    ProjectStatus,
    Task,
    TaskPriority,
    TaskSource,
    TaskStatus,
    TaskType,
)


@pytest.mark.asyncio
async def test_projects_summary_empty(client: AsyncClient) -> None:
    """Empty DB returns empty list."""
    resp = await client.get("/api/v1/dashboard/projects-summary")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_projects_summary_with_data(client: AsyncClient, db_session) -> None:
    """Returns correct task counts, bug counts, and phase info."""
    now = datetime.now(timezone.utc)

    project = Project(
        id=uuid.uuid4(), name="Test Project", description="desc",
        repo_path="/test", status=ProjectStatus.active,
        created_at=now, updated_at=now,
    )
    db_session.add(project)

    phase = Phase(
        id=uuid.uuid4(), project_id=project.id, name="Phase 1", order=1,
        status=PhaseStatus.active, branch_name="main",
        created_at=now, updated_at=now,
    )
    db_session.add(phase)
    await db_session.flush()

    # 2 ready tasks, 1 done task, 1 bug task
    for i, (status, task_type) in enumerate([
        (TaskStatus.ready, TaskType.feature),
        (TaskStatus.ready, TaskType.feature),
        (TaskStatus.done, TaskType.feature),
        (TaskStatus.ready, TaskType.bug),
    ]):
        db_session.add(Task(
            id=uuid.uuid4(), project_id=project.id, phase_id=phase.id,
            title=f"Task {i}", status=status, priority=TaskPriority.medium,
            task_type=task_type, source=TaskSource.architect,
            created_at=now, updated_at=now,
        ))
    await db_session.flush()

    resp = await client.get("/api/v1/dashboard/projects-summary")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1

    summary = data[0]
    assert summary["name"] == "Test Project"
    assert summary["status"] == "active"
    assert summary["current_phase"] == "Phase 1"
    assert summary["bug_count"] == 1
    assert summary["task_counts"]["ready"] == 3
    assert summary["task_counts"]["done"] == 1


@pytest.mark.asyncio
async def test_projects_summary_architect_session(client: AsyncClient, db_session) -> None:
    """Detects has_architect_session when project_bound session exists."""
    now = datetime.now(timezone.utc)

    project = Project(
        id=uuid.uuid4(), name="P", description="d", repo_path="/t",
        status=ProjectStatus.active, created_at=now, updated_at=now,
    )
    db_session.add(project)

    phase = Phase(
        id=uuid.uuid4(), project_id=project.id, name="Ph", order=1,
        status=PhaseStatus.active, branch_name="b",
        created_at=now, updated_at=now,
    )
    db_session.add(phase)

    session = DesignSession(
        id=uuid.uuid4(), status=DesignSessionStatus.project_bound,
        project_id=project.id, llm_config={},
        created_at=now, updated_at=now,
    )
    db_session.add(session)
    await db_session.flush()

    resp = await client.get("/api/v1/dashboard/projects-summary")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["has_architect_session"] is True


@pytest.mark.asyncio
async def test_projects_summary_multiple_projects(client: AsyncClient, db_session) -> None:
    """Returns summary for multiple projects."""
    now = datetime.now(timezone.utc)

    # Create multiple projects with different statuses
    for status in (ProjectStatus.active, ProjectStatus.design, ProjectStatus.completed):
        project = Project(
            id=uuid.uuid4(), name=f"P-{status.value}", description="d", repo_path="/t",
            status=status, created_at=now, updated_at=now,
        )
        db_session.add(project)

    await db_session.flush()

    resp = await client.get("/api/v1/dashboard/projects-summary")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3
