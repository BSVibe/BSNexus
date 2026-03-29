"""Tests for the planner approval workflow API endpoints."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.models import (
    Phase,
    PhaseStatus,
    Project,
    ProjectStatus,
    SuggestionStatus,
    TaskSuggestion,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def project(db_session: AsyncSession) -> Project:
    """Create a test project."""
    p = Project(
        name="Test Project",
        description="A test project",
        repo_path="/tmp/test-repo",
        status=ProjectStatus.active,
    )
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    return p


@pytest_asyncio.fixture
async def phase(db_session: AsyncSession, project: Project) -> Phase:
    """Create a test phase."""
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
async def suggestion(db_session: AsyncSession, project: Project) -> TaskSuggestion:
    """Create a pending task suggestion."""
    s = TaskSuggestion(
        project_id=project.id,
        title="Implement auth middleware",
        description="Add JWT-based authentication middleware",
        task_type="feature",
        priority=1,
        estimated_effort="4h",
        reasoning="Security requirement",
        status=SuggestionStatus.pending,
    )
    db_session.add(s)
    await db_session.commit()
    await db_session.refresh(s)
    return s


@pytest_asyncio.fixture
async def multiple_suggestions(db_session: AsyncSession, project: Project) -> list[TaskSuggestion]:
    """Create multiple suggestions with different statuses."""
    suggestions = []
    for i, status in enumerate(
        [SuggestionStatus.pending, SuggestionStatus.pending, SuggestionStatus.approved, SuggestionStatus.rejected]
    ):
        s = TaskSuggestion(
            project_id=project.id,
            title=f"Task suggestion {i}",
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


# ── GET /api/v1/planner/suggestions ───────────────────────────────────


class TestListSuggestions:
    async def test_list_pending_suggestions(self, client, project, multiple_suggestions):
        resp = await client.get(f"/api/v1/planner/suggestions?project_id={project.id}")
        assert resp.status_code == 200
        data = resp.json()
        # Should return only pending suggestions by default
        assert len(data) == 2
        assert all(s["status"] == "pending" for s in data)

    async def test_list_suggestions_filter_by_status(self, client, project, multiple_suggestions):
        resp = await client.get(f"/api/v1/planner/suggestions?project_id={project.id}&status=approved")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["status"] == "approved"

    async def test_list_suggestions_requires_project_id(self, client):
        resp = await client.get("/api/v1/planner/suggestions")
        assert resp.status_code == 422


# ── POST /api/v1/planner/suggestions/{id}/approve ────────────────────


class TestApproveSuggestion:
    async def test_approve_creates_task(self, client, db_session, suggestion, phase):
        resp = await client.post(
            f"/api/v1/planner/suggestions/{suggestion.id}/approve",
            json={"phase_id": str(phase.id)},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == suggestion.title
        assert data["description"] == suggestion.description
        assert data["phase_id"] == str(phase.id)
        assert data["project_id"] == str(suggestion.project_id)

    async def test_approve_updates_suggestion_status(self, client, db_session, suggestion, phase):
        await client.post(
            f"/api/v1/planner/suggestions/{suggestion.id}/approve",
            json={"phase_id": str(phase.id)},
        )
        await db_session.refresh(suggestion)
        assert suggestion.status == SuggestionStatus.approved

    async def test_approve_nonexistent_returns_404(self, client, phase):
        resp = await client.post(
            f"/api/v1/planner/suggestions/{uuid.uuid4()}/approve",
            json={"phase_id": str(phase.id)},
        )
        assert resp.status_code == 404

    async def test_approve_already_approved_returns_400(self, client, db_session, suggestion, phase):
        suggestion.status = SuggestionStatus.approved
        await db_session.commit()
        resp = await client.post(
            f"/api/v1/planner/suggestions/{suggestion.id}/approve",
            json={"phase_id": str(phase.id)},
        )
        assert resp.status_code == 400

    async def test_approve_invalid_phase_returns_404(self, client, suggestion):
        resp = await client.post(
            f"/api/v1/planner/suggestions/{suggestion.id}/approve",
            json={"phase_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 404


# ── POST /api/v1/planner/suggestions/{id}/reject ─────────────────────


class TestRejectSuggestion:
    async def test_reject_sets_status_and_reason(self, client, db_session, suggestion):
        resp = await client.post(
            f"/api/v1/planner/suggestions/{suggestion.id}/reject",
            json={"reason": "Not relevant to current sprint"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "rejected"
        assert data["rejection_reason"] == "Not relevant to current sprint"

    async def test_reject_nonexistent_returns_404(self, client):
        resp = await client.post(
            f"/api/v1/planner/suggestions/{uuid.uuid4()}/reject",
            json={"reason": "Not needed"},
        )
        assert resp.status_code == 404

    async def test_reject_already_rejected_returns_400(self, client, db_session, suggestion):
        suggestion.status = SuggestionStatus.rejected
        await db_session.commit()
        resp = await client.post(
            f"/api/v1/planner/suggestions/{suggestion.id}/reject",
            json={"reason": "Duplicate"},
        )
        assert resp.status_code == 400

    async def test_reject_requires_reason(self, client, suggestion):
        resp = await client.post(
            f"/api/v1/planner/suggestions/{suggestion.id}/reject",
            json={},
        )
        assert resp.status_code == 422


# ── POST /api/v1/planner/suggestions/{id}/modify ─────────────────────


class TestModifySuggestion:
    async def test_modify_updates_fields_and_approves(self, client, db_session, suggestion, phase):
        resp = await client.post(
            f"/api/v1/planner/suggestions/{suggestion.id}/modify",
            json={
                "phase_id": str(phase.id),
                "title": "Updated title",
                "priority": 5,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        # Returns the created Task with modified fields
        assert data["title"] == "Updated title"

    async def test_modify_updates_suggestion_status_to_modified(self, client, db_session, suggestion, phase):
        await client.post(
            f"/api/v1/planner/suggestions/{suggestion.id}/modify",
            json={"phase_id": str(phase.id), "title": "Changed"},
        )
        await db_session.refresh(suggestion)
        assert suggestion.status == SuggestionStatus.modified

    async def test_modify_nonexistent_returns_404(self, client, phase):
        resp = await client.post(
            f"/api/v1/planner/suggestions/{uuid.uuid4()}/modify",
            json={"phase_id": str(phase.id), "title": "New"},
        )
        assert resp.status_code == 404

    async def test_modify_non_pending_returns_400(self, client, db_session, suggestion, phase):
        suggestion.status = SuggestionStatus.approved
        await db_session.commit()
        resp = await client.post(
            f"/api/v1/planner/suggestions/{suggestion.id}/modify",
            json={"phase_id": str(phase.id), "title": "New"},
        )
        assert resp.status_code == 400


# ── POST /api/v1/planner/generate ────────────────────────────────────


class TestGeneratePlan:
    async def test_generate_calls_planner_service(self, client, project):
        mock_suggestions = [
            MagicMock(
                id=uuid.uuid4(),
                project_id=project.id,
                title="Generated task",
                description="Auto-generated",
                task_type="feature",
                priority=1,
                estimated_effort="2h",
                reasoning="Important",
                status=SuggestionStatus.pending,
                rejection_reason=None,
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
            )
        ]

        with patch("backend.src.api.planner.PlannerService") as mock_cls:
            mock_service = AsyncMock()
            mock_service.generate_daily_plan.return_value = mock_suggestions
            mock_cls.return_value = mock_service

            resp = await client.post(
                "/api/v1/planner/generate",
                json={"project_id": str(project.id)},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["title"] == "Generated task"

    async def test_generate_nonexistent_project_returns_404(self, client):
        with patch("backend.src.api.planner.PlannerService") as _mock_cls:
            resp = await client.post(
                "/api/v1/planner/generate",
                json={"project_id": str(uuid.uuid4())},
            )
        assert resp.status_code == 404
