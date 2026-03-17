from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from backend.src.main import app
from backend.src.models import (
    DesignMessage,
    DesignSession,
    DesignSessionStatus,
    MessageRole,
    MessageType,
    Phase,
    PhaseStatus,
    Project,
    ProjectStatus,
    Task,
    TaskPriority,
    TaskStatus,
)


# -- Helpers -------------------------------------------------------------------


async def create_project_with_children(db_session) -> tuple[Project, Phase, Task]:
    """Create a project with a phase and task for cascade-delete testing."""
    now = datetime.now(timezone.utc)
    project = Project(
        id=uuid.uuid4(),
        name="Delete Me",
        description="Project to delete",
        repo_path="/tmp/delete-me",
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
        title="Test Task",
        status=TaskStatus.ready,
        priority=TaskPriority.medium,
        version=1,
        created_at=now,
        updated_at=now,
    )
    db_session.add(task)
    await db_session.commit()

    return project, phase, task


async def create_session_with_messages(db_session) -> tuple[DesignSession, list[DesignMessage]]:
    """Create a design session with messages for cascade-delete testing."""
    now = datetime.now(timezone.utc)
    session = DesignSession(
        id=uuid.uuid4(),
        status=DesignSessionStatus.active,
        llm_config={"api_key": "sk-test"},
        created_at=now,
        updated_at=now,
    )
    db_session.add(session)
    await db_session.flush()

    messages = []
    for role, content in [
        (MessageRole.user, "Hello"),
        (MessageRole.assistant, "Hi there!"),
    ]:
        msg = DesignMessage(
            id=uuid.uuid4(),
            session_id=session.id,
            role=role,
            content=content,
            message_type=MessageType.chat,
            created_at=now,
        )
        db_session.add(msg)
        messages.append(msg)

    await db_session.commit()
    return session, messages


# -- Fixtures ------------------------------------------------------------------


@pytest.fixture(autouse=True)
def mock_redis():
    """Set app.state.redis to an AsyncMock for all tests."""
    mock = AsyncMock()
    app.state.redis = mock
    yield mock


# -- Project Delete Tests ------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_project(client, db_session):
    """DELETE /api/v1/projects/{id} removes the project and cascades to children."""
    project, phase, task = await create_project_with_children(db_session)

    response = await client.delete(f"/api/v1/projects/{project.id}")
    assert response.status_code == 200
    assert response.json()["detail"] == "Project deleted"

    # Verify project is gone
    get_response = await client.get(f"/api/v1/projects/{project.id}")
    assert get_response.status_code == 404


@pytest.mark.asyncio
async def test_delete_project_not_found(client):
    """DELETE /api/v1/projects/{id} returns 404 for non-existent project."""
    fake_id = uuid.uuid4()
    response = await client.delete(f"/api/v1/projects/{fake_id}")
    assert response.status_code == 404
    assert response.json()["detail"] == "Project not found"


# -- Session Delete Tests ------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_session(client, db_session):
    """DELETE /api/v1/architect/sessions/{id} removes the session and its messages."""
    session, messages = await create_session_with_messages(db_session)

    response = await client.delete(f"/api/v1/architect/sessions/{session.id}")
    assert response.status_code == 200
    assert response.json()["detail"] == "Session deleted"

    # Verify session is gone
    get_response = await client.get(f"/api/v1/architect/sessions/{session.id}")
    assert get_response.status_code == 404


@pytest.mark.asyncio
async def test_delete_session_not_found(client):
    """DELETE /api/v1/architect/sessions/{id} returns 404 for non-existent session."""
    fake_id = uuid.uuid4()
    response = await client.delete(f"/api/v1/architect/sessions/{fake_id}")
    assert response.status_code == 404
    assert response.json()["detail"] == "Session not found"


@pytest.mark.asyncio
async def test_delete_project_bound_session_keeps_project(client, db_session):
    """Deleting a project_bound session does not delete the linked project."""
    now = datetime.now(timezone.utc)

    project = Project(
        id=uuid.uuid4(),
        name="Keep Me",
        description="Should survive session delete",
        repo_path="/tmp/keep",
        status=ProjectStatus.active,
        created_at=now,
        updated_at=now,
    )
    db_session.add(project)
    await db_session.flush()

    session = DesignSession(
        id=uuid.uuid4(),
        project_id=project.id,
        status=DesignSessionStatus.project_bound,
        llm_config={"api_key": "sk-test"},
        created_at=now,
        updated_at=now,
    )
    db_session.add(session)
    await db_session.commit()

    # Delete the session
    response = await client.delete(f"/api/v1/architect/sessions/{session.id}")
    assert response.status_code == 200

    # Project should still exist
    get_response = await client.get(f"/api/v1/projects/{project.id}")
    assert get_response.status_code == 200


# -- Batch Delete Tests --------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_delete_projects(client, db_session):
    """POST /api/v1/projects/batch-delete removes multiple projects."""
    p1, _, _ = await create_project_with_children(db_session)
    p2, _, _ = await create_project_with_children(db_session)

    response = await client.post(
        "/api/v1/projects/batch-delete",
        json={"ids": [str(p1.id), str(p2.id)]},
    )
    assert response.status_code == 200
    assert response.json()["deleted"] == 2

    # Verify both are gone
    assert (await client.get(f"/api/v1/projects/{p1.id}")).status_code == 404
    assert (await client.get(f"/api/v1/projects/{p2.id}")).status_code == 404


@pytest.mark.asyncio
async def test_batch_delete_projects_partial(client, db_session):
    """POST /api/v1/projects/batch-delete skips non-existent IDs."""
    project, _, _ = await create_project_with_children(db_session)
    fake_id = uuid.uuid4()

    response = await client.post(
        "/api/v1/projects/batch-delete",
        json={"ids": [str(project.id), str(fake_id)]},
    )
    assert response.status_code == 200
    assert response.json()["deleted"] == 1


@pytest.mark.asyncio
async def test_batch_delete_sessions(client, db_session):
    """POST /api/v1/architect/sessions/batch-delete removes multiple sessions."""
    s1, _ = await create_session_with_messages(db_session)
    s2, _ = await create_session_with_messages(db_session)

    response = await client.post(
        "/api/v1/architect/sessions/batch-delete",
        json={"ids": [str(s1.id), str(s2.id)]},
    )
    assert response.status_code == 200
    assert response.json()["deleted"] == 2

    # Verify both are gone
    assert (await client.get(f"/api/v1/architect/sessions/{s1.id}")).status_code == 404
    assert (await client.get(f"/api/v1/architect/sessions/{s2.id}")).status_code == 404


@pytest.mark.asyncio
async def test_batch_delete_sessions_partial(client, db_session):
    """POST /api/v1/architect/sessions/batch-delete skips non-existent IDs."""
    session, _ = await create_session_with_messages(db_session)
    fake_id = uuid.uuid4()

    response = await client.post(
        "/api/v1/architect/sessions/batch-delete",
        json={"ids": [str(session.id), str(fake_id)]},
    )
    assert response.status_code == 200
    assert response.json()["deleted"] == 1
