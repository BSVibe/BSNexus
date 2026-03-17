"""Tests for project_bound session messaging and status transitions."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from backend.src.models import (
    DesignSession,
    DesignSessionStatus,
    Phase,
    PhaseStatus,
    Project,
    ProjectStatus,
    Setting,
)


LLM_CONFIG = {"api_key": "sk-test", "model": "gpt-4o"}


async def _setup_project_bound_session(db_session) -> tuple:
    """Create a project with a project_bound design session."""
    now = datetime.now(timezone.utc)

    project = Project(
        id=uuid.uuid4(), name="Test", description="d", repo_path="/t",
        status=ProjectStatus.active, created_at=now, updated_at=now,
    )
    db_session.add(project)

    phase = Phase(
        id=uuid.uuid4(), project_id=project.id, name="Phase 1", order=1,
        status=PhaseStatus.active, branch_name="main",
        created_at=now, updated_at=now,
    )
    db_session.add(phase)

    session = DesignSession(
        id=uuid.uuid4(), status=DesignSessionStatus.project_bound,
        project_id=project.id, llm_config=LLM_CONFIG,
        created_at=now, updated_at=now,
    )
    db_session.add(session)

    # LLM settings
    db_session.add(Setting(key="llm_api_key", value="sk-test"))
    db_session.add(Setting(key="llm_model", value="gpt-4o"))
    await db_session.commit()

    return project, phase, session


@pytest.mark.asyncio
async def test_project_bound_session_accepts_messages(client: AsyncClient, db_session) -> None:
    """project_bound sessions can receive messages (not rejected like cancelled)."""
    project, phase, session = await _setup_project_bound_session(db_session)

    with patch("backend.src.api.architect.LLMClient") as MockLLM:
        instance = MockLLM.return_value
        instance.chat = AsyncMock(return_value="I can help with that!")

        resp = await client.post(
            f"/api/v1/architect/sessions/{session.id}/message",
            json={"content": "Add a new login feature"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["role"] == "assistant"
    assert "I can help" in data["content"]


@pytest.mark.asyncio
async def test_cancelled_session_rejects_messages(client: AsyncClient, db_session) -> None:
    """Cancelled sessions return 400."""
    now = datetime.now(timezone.utc)

    session = DesignSession(
        id=uuid.uuid4(), status=DesignSessionStatus.cancelled,
        llm_config=LLM_CONFIG, created_at=now, updated_at=now,
    )
    db_session.add(session)
    db_session.add(Setting(key="llm_api_key", value="sk-test"))
    db_session.add(Setting(key="llm_model", value="gpt-4o"))
    await db_session.commit()

    resp = await client.post(
        f"/api/v1/architect/sessions/{session.id}/message",
        json={"content": "hello"},
    )
    assert resp.status_code == 400
    assert "cancelled" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_get_session_by_project(client: AsyncClient, db_session) -> None:
    """GET /sessions/by-project/{project_id} returns the project-bound session."""
    project, phase, session = await _setup_project_bound_session(db_session)

    resp = await client.get(f"/api/v1/architect/sessions/by-project/{project.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(session.id)
    assert data["status"] == "project_bound"


@pytest.mark.asyncio
async def test_get_session_by_project_not_found(client: AsyncClient) -> None:
    """Returns 404 when no session exists for the project."""
    resp = await client.get(f"/api/v1/architect/sessions/by-project/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_project_bound_session_remains_project_bound(client: AsyncClient, db_session) -> None:
    """Sending a message doesn't change project_bound status."""
    project, phase, session = await _setup_project_bound_session(db_session)

    with patch("backend.src.api.architect.LLMClient") as MockLLM:
        instance = MockLLM.return_value
        instance.chat = AsyncMock(return_value="Done!")

        await client.post(
            f"/api/v1/architect/sessions/{session.id}/message",
            json={"content": "Update task priorities"},
        )

    await db_session.refresh(session)
    assert session.status == DesignSessionStatus.project_bound
