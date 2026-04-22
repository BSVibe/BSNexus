"""Tests for the task activity feed endpoint."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient

from backend.src.models import (
    ActivityLevel,
    Phase,
    PhaseStatus,
    Project,
    ProjectStatus,
    Task,
    TaskActivity,
    TaskHistory,
    TaskPriority,
    TaskSource,
    TaskStatus,
    TaskType,
)


async def _seed_task(db_session) -> Task:
    now = datetime.now(timezone.utc)
    project = Project(
        id=uuid.uuid4(),
        name="Activity Project",
        description="",
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

    task = Task(
        id=uuid.uuid4(),
        project_id=project.id,
        phase_id=phase.id,
        title="Activity Task",
        status=TaskStatus.running,
        priority=TaskPriority.medium,
        task_type=TaskType.feature,
        source=TaskSource.llm,
        version=1,
        created_at=now,
        updated_at=now,
    )
    db_session.add(task)
    await db_session.flush()
    return task


async def test_activity_returns_404_for_missing_task(client: AsyncClient) -> None:
    resp = await client.get(f"/api/v1/tasks/{uuid.uuid4()}/activity")
    assert resp.status_code == 404


async def test_activity_returns_empty_feed(client: AsyncClient, db_session) -> None:
    task = await _seed_task(db_session)
    await db_session.commit()

    resp = await client.get(f"/api/v1/tasks/{task.id}/activity")
    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == str(task.id)
    assert data["entries"] == []


async def test_activity_returns_milestones_only_by_default(client: AsyncClient, db_session) -> None:
    task = await _seed_task(db_session)
    db_session.add(
        TaskActivity(
            task_id=task.id,
            project_id=task.project_id,
            level=ActivityLevel.milestone,
            event_type="task_started",
            summary="Started by worker-1",
            detail={"worker_id": "w1"},
        )
    )
    db_session.add(
        TaskActivity(
            task_id=task.id,
            project_id=task.project_id,
            level=ActivityLevel.tool,
            event_type="exec",
            summary="ran pytest",
            detail={"cmd": "pytest"},
        )
    )
    await db_session.commit()

    resp = await client.get(f"/api/v1/tasks/{task.id}/activity")
    data = resp.json()
    assert len(data["entries"]) == 1
    assert data["entries"][0]["event_type"] == "task_started"


async def test_activity_returns_all_levels_with_query(client: AsyncClient, db_session) -> None:
    task = await _seed_task(db_session)
    db_session.add(
        TaskActivity(
            task_id=task.id,
            project_id=task.project_id,
            level=ActivityLevel.milestone,
            event_type="task_started",
            summary="m1",
        )
    )
    db_session.add(
        TaskActivity(
            task_id=task.id,
            project_id=task.project_id,
            level=ActivityLevel.tool,
            event_type="exec",
            summary="t1",
        )
    )
    await db_session.commit()

    resp = await client.get(f"/api/v1/tasks/{task.id}/activity?level=all")
    data = resp.json()
    levels = sorted(e["level"] for e in data["entries"])
    assert levels == ["milestone", "tool"]


async def test_activity_includes_legacy_history_rows(client: AsyncClient, db_session) -> None:
    task = await _seed_task(db_session)
    db_session.add(
        TaskHistory(
            task_id=task.id,
            from_status="pending",
            to_status="running",
            actor="dispatcher",
            reason=None,
        )
    )
    await db_session.commit()

    resp = await client.get(f"/api/v1/tasks/{task.id}/activity")
    data = resp.json()
    assert any(e["event_type"] == "state_transition" for e in data["entries"])


async def test_activity_rejects_invalid_level(client: AsyncClient, db_session) -> None:
    task = await _seed_task(db_session)
    await db_session.commit()
    resp = await client.get(f"/api/v1/tasks/{task.id}/activity?level=garbage")
    assert resp.status_code == 422
