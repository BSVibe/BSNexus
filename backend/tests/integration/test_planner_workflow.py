"""Integration tests for the planner workflow — generate, list, approve, reject, modify, briefing."""

from __future__ import annotations

import uuid as uuid_mod
from unittest.mock import patch

from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.models import (
    Phase,
    PhaseStatus,
    SuggestionStatus,
    Task,
    TaskSuggestion,
)


# -- Helpers -------------------------------------------------------------------


async def _create_project(client: AsyncClient) -> dict:
    resp = await client.post(
        "/api/v1/projects",
        json={
            "name": "Planner Integration Project",
            "description": "Project for planner integration testing",
            "repo_path": "/test/planner",
        },
    )
    assert resp.status_code == 201
    return resp.json()


async def _create_phase(client: AsyncClient, project_id: str) -> dict:
    resp = await client.post(
        f"/api/v1/projects/{project_id}/phases",
        json={
            "name": "Planner Phase",
            "description": "Phase for planner testing",
            "order": 1,
        },
    )
    assert resp.status_code == 201
    return resp.json()


async def _activate_phase(db_session: AsyncSession, phase_id: str) -> None:
    await db_session.execute(update(Phase).where(Phase.id == uuid_mod.UUID(phase_id)).values(status=PhaseStatus.active))
    await db_session.flush()


# -- Tests ---------------------------------------------------------------------


class TestPlannerEndpointsAccessible:
    """Verify all planner endpoints are registered and accessible."""

    async def test_suggestions_endpoint_accessible(self, client: AsyncClient):
        resp = await client.get(f"/api/v1/planner/suggestions?project_id={uuid_mod.uuid4()}")
        # 200 with empty list (not 404 which would mean route not found)
        assert resp.status_code == 200

    async def test_briefing_endpoint_accessible(self, client: AsyncClient):
        resp = await client.get(f"/api/v1/planner/briefing?project_id={uuid_mod.uuid4()}")
        assert resp.status_code == 200

    async def test_approve_endpoint_accessible(self, client: AsyncClient):
        resp = await client.post(
            f"/api/v1/planner/suggestions/{uuid_mod.uuid4()}/approve",
            json={"phase_id": str(uuid_mod.uuid4())},
        )
        # 404 = route exists but suggestion not found
        assert resp.status_code == 404

    async def test_reject_endpoint_accessible(self, client: AsyncClient):
        resp = await client.post(
            f"/api/v1/planner/suggestions/{uuid_mod.uuid4()}/reject",
            json={"reason": "test"},
        )
        assert resp.status_code == 404

    async def test_modify_endpoint_accessible(self, client: AsyncClient):
        resp = await client.post(
            f"/api/v1/planner/suggestions/{uuid_mod.uuid4()}/modify",
            json={"phase_id": str(uuid_mod.uuid4()), "title": "t"},
        )
        assert resp.status_code == 404

    async def test_generate_endpoint_accessible(self, client: AsyncClient):
        # Generate with nonexistent project returns 404 (not route-not-found)
        with patch("backend.src.api.planner.PlannerService"):
            resp = await client.post(
                "/api/v1/planner/generate",
                json={"project_id": str(uuid_mod.uuid4())},
            )
        assert resp.status_code == 404


class TestPlannerFullWorkflow:
    """End-to-end: generate suggestions -> list -> approve one -> reject one -> verify briefing."""

    async def test_generate_approve_reject_workflow(self, client: AsyncClient, db_session: AsyncSession):
        # 1. Create project + phase
        project = await _create_project(client)
        phase = await _create_phase(client, project["id"])
        await _activate_phase(db_session, phase["id"])

        project_id = project["id"]

        # 2. Create suggestions directly in DB (simulating generate)
        s1 = TaskSuggestion(
            project_id=uuid_mod.UUID(project_id),
            title="Add authentication",
            description="Implement JWT auth",
            task_type="feature",
            priority=1,
            estimated_effort="4h",
            reasoning="Security requirement",
            status=SuggestionStatus.pending,
        )
        s2 = TaskSuggestion(
            project_id=uuid_mod.UUID(project_id),
            title="Write unit tests",
            description="Cover auth module",
            task_type="test",
            priority=2,
            estimated_effort="2h",
            reasoning="Quality assurance",
            status=SuggestionStatus.pending,
        )
        db_session.add_all([s1, s2])
        await db_session.commit()
        await db_session.refresh(s1)
        await db_session.refresh(s2)

        # 3. List pending suggestions
        resp = await client.get(f"/api/v1/planner/suggestions?project_id={project_id}")
        assert resp.status_code == 200
        suggestions = resp.json()
        assert len(suggestions) == 2

        # 4. Approve first suggestion (creates a real Task)
        approve_resp = await client.post(
            f"/api/v1/planner/suggestions/{s1.id}/approve",
            json={"phase_id": phase["id"]},
        )
        assert approve_resp.status_code == 200
        task_data = approve_resp.json()
        assert task_data["title"] == "Add authentication"
        assert task_data["status"] == "pending"
        assert task_data["phase_id"] == phase["id"]

        # 5. Reject second suggestion
        reject_resp = await client.post(
            f"/api/v1/planner/suggestions/{s2.id}/reject",
            json={"reason": "Not needed this sprint"},
        )
        assert reject_resp.status_code == 200
        assert reject_resp.json()["status"] == "rejected"

        # 6. List pending should be empty now
        resp = await client.get(f"/api/v1/planner/suggestions?project_id={project_id}")
        assert resp.status_code == 200
        assert len(resp.json()) == 0

        # 7. Verify a Task was actually created in the DB
        result = await db_session.execute(select(Task).where(Task.project_id == uuid_mod.UUID(project_id)))
        tasks = result.scalars().all()
        assert len(tasks) == 1
        assert tasks[0].title == "Add authentication"

    async def test_modify_then_verify_task_created(self, client: AsyncClient, db_session: AsyncSession):
        """Modify a suggestion and verify it creates a Task with updated fields."""
        project = await _create_project(client)
        phase = await _create_phase(client, project["id"])
        await _activate_phase(db_session, phase["id"])

        suggestion = TaskSuggestion(
            project_id=uuid_mod.UUID(project["id"]),
            title="Original title",
            description="Original description",
            task_type="feature",
            priority=5,
            status=SuggestionStatus.pending,
        )
        db_session.add(suggestion)
        await db_session.commit()
        await db_session.refresh(suggestion)

        # Modify and approve
        resp = await client.post(
            f"/api/v1/planner/suggestions/{suggestion.id}/modify",
            json={
                "phase_id": phase["id"],
                "title": "Modified title",
                "priority": 1,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Modified title"

        # Suggestion status should be "modified"
        await db_session.refresh(suggestion)
        assert suggestion.status == SuggestionStatus.modified

    async def test_briefing_aggregates_data(self, client: AsyncClient, db_session: AsyncSession):
        """Verify briefing returns correct aggregation."""
        project = await _create_project(client)
        phase = await _create_phase(client, project["id"])
        await _activate_phase(db_session, phase["id"])

        project_id = project["id"]

        # Add suggestions
        s1 = TaskSuggestion(
            project_id=uuid_mod.UUID(project_id),
            title="Pending task",
            task_type="feature",
            priority=1,
            status=SuggestionStatus.pending,
        )
        db_session.add(s1)
        await db_session.commit()

        # Get briefing
        resp = await client.get(f"/api/v1/planner/briefing?project_id={project_id}")
        assert resp.status_code == 200
        briefing = resp.json()
        assert "suggestions" in briefing
        assert "pending_count" in briefing
        assert "approved_today" in briefing
        assert "total_tasks_active" in briefing
        assert briefing["pending_count"] >= 1


class TestPlannerSettingsIntegration:
    """Verify planner settings are available in the app config."""

    def test_settings_have_llm_fields(self):
        from backend.src.config import settings

        assert hasattr(settings, "default_llm_model")
        assert isinstance(settings.default_llm_model, str)
