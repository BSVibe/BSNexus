"""Tests for the morning briefing endpoint GET /api/v1/planner/briefing."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.models import (
    Phase,
    PhaseStatus,
    Project,
    ProjectStatus,
    SuggestionStatus,
    Task,
    TaskSuggestion,
    TaskStatus,
    TaskType,
    TaskPriority,
    TaskSource,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def project(db_session: AsyncSession) -> Project:
    p = Project(
        name="Briefing Project",
        description="Project for briefing tests",
        repo_path="/tmp/briefing-repo",
        status=ProjectStatus.active,
    )
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    return p


@pytest_asyncio.fixture
async def phase(db_session: AsyncSession, project: Project) -> Phase:
    ph = Phase(
        project_id=project.id,
        name="Phase 1",
        description="First phase",
        branch_name="feat/phase-1",
        order=1,
        status=PhaseStatus.active,
    )
    db_session.add(ph)
    await db_session.commit()
    await db_session.refresh(ph)
    return ph


@pytest_asyncio.fixture
async def suggestions_mixed(db_session: AsyncSession, project: Project) -> list[TaskSuggestion]:
    """Create suggestions: 2 pending, 1 approved (today), 1 rejected."""
    suggestions = []
    statuses = [
        SuggestionStatus.pending,
        SuggestionStatus.pending,
        SuggestionStatus.approved,
        SuggestionStatus.rejected,
    ]
    for i, status in enumerate(statuses):
        s = TaskSuggestion(
            project_id=project.id,
            title=f"Suggestion {i}",
            description=f"Description {i}",
            task_type="feature",
            priority=i + 1,
            status=status,
        )
        db_session.add(s)
        suggestions.append(s)
    await db_session.commit()
    for s in suggestions:
        await db_session.refresh(s)
    return suggestions


@pytest_asyncio.fixture
async def active_tasks(db_session: AsyncSession, project: Project, phase: Phase) -> list[Task]:
    """Create tasks with various statuses: 2 in_progress, 1 ready, 1 done."""
    tasks = []
    statuses_list = [TaskStatus.in_progress, TaskStatus.in_progress, TaskStatus.ready, TaskStatus.done]
    for i, status in enumerate(statuses_list):
        t = Task(
            project_id=project.id,
            phase_id=phase.id,
            title=f"Task {i}",
            description=f"Task description {i}",
            status=status,
            priority=TaskPriority.medium,
            task_type=TaskType.feature,
            source=TaskSource.architect,
        )
        db_session.add(t)
        tasks.append(t)
    await db_session.commit()
    for t in tasks:
        await db_session.refresh(t)
    return tasks


# ── GET /api/v1/planner/briefing ─────────────────────────────────────


class TestBriefingEndpoint:
    async def test_briefing_returns_structured_json(self, client, project, suggestions_mixed, active_tasks):
        resp = await client.get(f"/api/v1/planner/briefing?project_id={project.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert "suggestions" in data
        assert "pending_count" in data
        assert "approved_today" in data
        assert "total_tasks_active" in data

    async def test_briefing_suggestions_are_todays_only(self, client, project, suggestions_mixed):
        resp = await client.get(f"/api/v1/planner/briefing?project_id={project.id}")
        assert resp.status_code == 200
        data = resp.json()
        # All 4 suggestions were created today
        assert len(data["suggestions"]) == 4

    async def test_briefing_pending_count(self, client, project, suggestions_mixed):
        resp = await client.get(f"/api/v1/planner/briefing?project_id={project.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["pending_count"] == 2

    async def test_briefing_approved_today_count(self, client, project, suggestions_mixed):
        resp = await client.get(f"/api/v1/planner/briefing?project_id={project.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["approved_today"] == 1

    async def test_briefing_total_tasks_active(self, client, project, suggestions_mixed, active_tasks):
        resp = await client.get(f"/api/v1/planner/briefing?project_id={project.id}")
        assert resp.status_code == 200
        data = resp.json()
        # in_progress (2) + ready (1) = 3 active tasks (done is not active)
        assert data["total_tasks_active"] == 3

    async def test_briefing_requires_project_id(self, client):
        resp = await client.get("/api/v1/planner/briefing")
        assert resp.status_code == 422

    async def test_briefing_empty_project(self, client, project):
        """Briefing for a project with no suggestions or tasks."""
        resp = await client.get(f"/api/v1/planner/briefing?project_id={project.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["suggestions"] == []
        assert data["pending_count"] == 0
        assert data["approved_today"] == 0
        assert data["total_tasks_active"] == 0

    async def test_briefing_nonexistent_project(self, client):
        """Briefing for a nonexistent project still returns data (empty)."""
        resp = await client.get(f"/api/v1/planner/briefing?project_id={uuid.uuid4()}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["suggestions"] == []
        assert data["pending_count"] == 0
