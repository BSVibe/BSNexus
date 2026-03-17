from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from backend.src.models import (
    DesignSession,
    DesignSessionStatus,
    MessageRole,
    MessageType,
    Phase,
    PhaseStatus,
    Project,
    ProjectStatus,
    Setting,
    Worker,
    WorkerStatus,
)
from fastapi import HTTPException
from httpx import AsyncClient

# ── Helpers ──────────────────────────────────────────────────────────


LLM_CONFIG = {"api_key": "sk-test-key-1234", "model": "gpt-4o"}


async def insert_global_llm_settings(
    db_session,
    api_key: str = "sk-test-key-1234",
    model: str = "gpt-4o",
    base_url: str | None = None,
) -> None:
    """Insert global LLM settings into the Setting table."""
    db_session.add(Setting(key="llm_api_key", value=api_key))
    db_session.add(Setting(key="llm_model", value=model))
    if base_url:
        db_session.add(Setting(key="llm_base_url", value=base_url))
    await db_session.commit()


async def create_session_via_api(client: AsyncClient, db_session=None) -> dict:
    """Helper to create a session through the API.

    Requires global LLM settings in DB. If db_session is provided and no settings exist,
    they will be inserted automatically.
    """
    if db_session is not None:
        from sqlalchemy import select

        result = await db_session.execute(
            select(Setting).where(Setting.key == "llm_api_key")
        )
        if result.scalar_one_or_none() is None:
            await insert_global_llm_settings(db_session)

    response = await client.post(
        "/api/v1/architect/sessions",
        json={},
    )
    assert response.status_code == 201
    return response.json()


async def create_session_in_db(db_session) -> DesignSession:
    """Helper to create a session directly in DB."""
    now = datetime.now(timezone.utc)
    session = DesignSession(
        id=uuid.uuid4(),
        status=DesignSessionStatus.active,
        llm_config=LLM_CONFIG,
        created_at=now,
        updated_at=now,
    )
    db_session.add(session)
    await db_session.commit()
    return session


async def create_project_with_phase(db_session) -> tuple[Project, Phase]:
    """Helper to create a project and phase for add-task tests."""
    now = datetime.now(timezone.utc)
    project = Project(
        id=uuid.uuid4(),
        name="Test Project",
        description="Test Description",
        repo_path="/test/repo",
        status=ProjectStatus.active,
        llm_config={"architect": LLM_CONFIG},
        created_at=now,
        updated_at=now,
    )
    db_session.add(project)

    phase = Phase(
        id=uuid.uuid4(),
        project_id=project.id,
        name="Phase 1",
        description="First phase",
        branch_name="phase/phase-1",
        order=1,
        status=PhaseStatus.active,
        created_at=now,
        updated_at=now,
    )
    db_session.add(phase)
    await db_session.commit()
    return project, phase


# ── List Sessions Tests ──────────────────────────────────────────────


async def test_list_sessions_empty(client: AsyncClient, db_session):
    """GET /api/architect/sessions returns empty list when no sessions exist."""
    response = await client.get("/api/v1/architect/sessions")
    assert response.status_code == 200
    assert response.json() == []


async def test_list_sessions_returns_all(client: AsyncClient, db_session):
    """GET /api/architect/sessions returns all sessions ordered by created_at desc."""
    await create_session_via_api(client, db_session)
    await create_session_via_api(client, db_session)

    response = await client.get("/api/v1/architect/sessions")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    # Each session should have messages field
    for s in data:
        assert "messages" in s


async def test_list_sessions_filter_by_status(client: AsyncClient, db_session):
    """GET /api/architect/sessions?status=active returns only active sessions."""
    _ = await create_session_via_api(client, db_session)

    # Create and mark another session as project_bound
    session2 = await create_session_in_db(db_session)
    session2.status = DesignSessionStatus.project_bound
    await db_session.commit()

    response = await client.get(
        "/api/v1/architect/sessions", params={"status": "active"}
    )
    assert response.status_code == 200
    data = response.json()
    assert all(s["status"] == "active" for s in data)


async def test_list_sessions_invalid_status(client: AsyncClient, db_session):
    """GET /api/architect/sessions?status=invalid returns 400."""
    response = await client.get(
        "/api/v1/architect/sessions", params={"status": "invalid"}
    )
    assert response.status_code == 400
    assert "Invalid status" in response.json()["detail"]


async def test_list_sessions_with_messages_for_resume(client: AsyncClient, db_session):
    """Sessions list includes messages so user can resume conversations."""
    session_data = await create_session_via_api(client, db_session)
    session_id = session_data["id"]

    # Send a message to build history
    with patch("backend.src.api.architect.LLMClient") as MockClient:
        instance = MockClient.return_value
        instance.chat = AsyncMock(return_value="I can help!")
        await client.post(
            f"/api/v1/architect/sessions/{session_id}/message",
            json={"content": "Help me design"},
        )

    # List sessions - should include messages for resume
    response = await client.get("/api/v1/architect/sessions")
    assert response.status_code == 200
    data = response.json()
    target = [s for s in data if s["id"] == session_id][0]
    assert len(target["messages"]) == 2  # user + assistant


# ── Session Creation Tests ───────────────────────────────────────────


async def test_create_session_success(client: AsyncClient, db_session):
    """POST /api/architect/sessions returns 201 when global LLM settings exist."""
    data = await create_session_via_api(client, db_session)

    assert "id" in data
    assert data["status"] == "active"
    assert data["project_id"] is None
    assert len(data["messages"]) == 0


async def test_create_session_stores_worker_id(client: AsyncClient, db_session):
    """POST /api/architect/sessions stores worker_id when provided."""
    await insert_global_llm_settings(db_session)
    worker_id = uuid.uuid4()

    # Insert worker in DB so FK constraint is satisfied
    db_session.add(Worker(
        id=worker_id, name="w1", platform="linux",
        executor_type="claude-code", status=WorkerStatus.idle,
        registered_at=datetime.now(timezone.utc),
    ))
    await db_session.commit()

    response = await client.post(
        "/api/v1/architect/sessions",
        json={"worker_id": str(worker_id)},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["worker_id"] == str(worker_id)


async def test_create_session_without_worker_id(client: AsyncClient, db_session):
    """POST /api/architect/sessions without worker_id stores None."""
    data = await create_session_via_api(client, db_session)
    assert data.get("worker_id") is None


async def test_create_session_uses_global_settings(client: AsyncClient, db_session):
    """POST /api/architect/sessions uses global LLM settings from DB."""
    await insert_global_llm_settings(
        db_session,
        api_key="sk-custom",
        model="custom-model",
        base_url="https://custom.api",
    )
    response = await client.post(
        "/api/v1/architect/sessions",
        json={},
    )
    assert response.status_code == 201
    data = response.json()
    # Verify we can retrieve it
    get_resp = await client.get(f"/api/v1/architect/sessions/{data['id']}")
    assert get_resp.status_code == 200


async def test_create_session_minimal_global_settings(client: AsyncClient, db_session):
    """POST /api/architect/sessions works with only api_key in global settings."""
    await insert_global_llm_settings(db_session, api_key="sk-minimal", model="")
    response = await client.post(
        "/api/v1/architect/sessions",
        json={},
    )
    assert response.status_code == 201


async def test_create_session_missing_api_key(client: AsyncClient, db_session):
    """POST /api/architect/sessions returns 400 when no global LLM API key is configured."""
    response = await client.post(
        "/api/v1/architect/sessions",
        json={},
    )
    assert response.status_code == 400
    assert "LLM API key not configured" in response.json()["detail"]


# ── Get Session Tests ────────────────────────────────────────────────


async def test_get_session_success(client: AsyncClient, db_session):
    """GET /api/architect/sessions/{id} returns 200 with messages."""
    data = await create_session_via_api(client, db_session)
    session_id = data["id"]

    response = await client.get(f"/api/v1/architect/sessions/{session_id}")

    assert response.status_code == 200
    session_data = response.json()
    assert session_data["id"] == session_id
    assert session_data["status"] == "active"
    assert isinstance(session_data["messages"], list)


async def test_get_session_not_found(client: AsyncClient, db_session):
    """GET /api/architect/sessions/{random_id} returns 404."""
    random_id = str(uuid.uuid4())
    response = await client.get(f"/api/v1/architect/sessions/{random_id}")
    assert response.status_code == 404
    assert response.json()["detail"] == "Session not found"


# ── REST Message Tests ───────────────────────────────────────────────


async def test_send_message_success(client: AsyncClient, db_session):
    """POST /api/architect/sessions/{id}/message returns assistant response."""
    session_data = await create_session_via_api(client, db_session)
    session_id = session_data["id"]

    mock_response = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = "I can help with that!"
    mock_response.choices = [mock_choice]

    with patch("backend.src.api.architect.LLMClient") as MockClient:
        instance = MockClient.return_value
        instance.chat = AsyncMock(return_value="I can help with that!")

        response = await client.post(
            f"/api/v1/architect/sessions/{session_id}/message",
            json={"content": "Help me design an API"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["role"] == "assistant"
    assert data["content"] == "I can help with that!"
    assert data["session_id"] == session_id


async def test_send_message_saves_user_message(client: AsyncClient, db_session):
    """POST /api/architect/sessions/{id}/message saves user and assistant messages."""
    session_data = await create_session_via_api(client, db_session)
    session_id = session_data["id"]

    with patch("backend.src.api.architect.LLMClient") as MockClient:
        instance = MockClient.return_value
        instance.chat = AsyncMock(return_value="Response")

        await client.post(
            f"/api/v1/architect/sessions/{session_id}/message",
            json={"content": "Hello"},
        )

    # Verify both messages are saved
    get_resp = await client.get(f"/api/v1/architect/sessions/{session_id}")
    messages = get_resp.json()["messages"]
    # user msg + assistant msg = 2
    assert len(messages) == 2
    roles = [m["role"] for m in messages]
    assert "user" in roles
    assert roles.count("assistant") == 1  # LLM response only


async def test_send_message_session_not_found(client: AsyncClient, db_session):
    """POST /api/architect/sessions/{random_id}/message returns 404."""
    random_id = str(uuid.uuid4())
    response = await client.post(
        f"/api/v1/architect/sessions/{random_id}/message",
        json={"content": "Hello"},
    )
    assert response.status_code == 404


async def test_send_message_cancelled_session(client: AsyncClient, db_session):
    """POST /api/architect/sessions/{id}/message on cancelled session returns 400."""
    session = await create_session_in_db(db_session)

    session.status = DesignSessionStatus.cancelled
    await db_session.commit()

    response = await client.post(
        f"/api/v1/architect/sessions/{session.id}/message",
        json={"content": "Hello"},
    )
    assert response.status_code == 400
    assert "cancelled" in response.json()["detail"]


async def test_send_message_llm_error(client: AsyncClient, db_session):
    """POST /api/architect/sessions/{id}/message returns 502 on LLM failure."""
    session_data = await create_session_via_api(client, db_session)
    session_id = session_data["id"]

    with patch("backend.src.api.architect.LLMClient") as MockClient:
        from backend.src.core.llm_client import LLMError

        instance = MockClient.return_value
        instance.chat = AsyncMock(side_effect=LLMError("API rate limited"))

        response = await client.post(
            f"/api/v1/architect/sessions/{session_id}/message",
            json={"content": "Hello"},
        )

    assert response.status_code == 502
    assert "LLM error" in response.json()["detail"]


# ── SSE Streaming Message Tests ──────────────────────────────────────


async def test_stream_message_success(client: AsyncClient, db_session):
    """POST /api/architect/sessions/{id}/message/stream returns SSE response."""
    session_data = await create_session_via_api(client, db_session)
    session_id = session_data["id"]

    async def mock_stream_chat(messages, **kwargs):
        for chunk in ["Hello", " world", "!"]:
            yield chunk

    with patch("backend.src.api.architect.LLMClient") as MockClient:
        instance = MockClient.return_value
        instance.stream_chat = mock_stream_chat

        response = await client.post(
            f"/api/v1/architect/sessions/{session_id}/message/stream",
            json={"content": "Hello"},
        )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")


async def test_stream_message_session_not_found(client: AsyncClient, db_session):
    """POST /api/architect/sessions/{random_id}/message/stream returns 404."""
    random_id = str(uuid.uuid4())
    response = await client.post(
        f"/api/v1/architect/sessions/{random_id}/message/stream",
        json={"content": "Hello"},
    )
    assert response.status_code == 404


async def test_stream_message_cancelled_session(client: AsyncClient, db_session):
    """POST /api/architect/sessions/{id}/message/stream on cancelled session returns 400."""
    session = await create_session_in_db(db_session)
    session.status = DesignSessionStatus.cancelled
    await db_session.commit()

    response = await client.post(
        f"/api/v1/architect/sessions/{session.id}/message/stream",
        json={"content": "Hello"},
    )
    assert response.status_code == 400


# ── Finalize Design Tests ────────────────────────────────────────────


MOCK_FINALIZE_RESPONSE = {
    "project_name": "My Project",
    "project_description": "A test project",
    "phases": [
        {
            "name": "Phase 1: Setup",
            "description": "Initial setup",
            "tasks": [
                {
                    "title": "Setup environment",
                    "description": "Configure dev environment",
                    "priority": "high",
                    "depends_on_indices": [],
                    "worker_prompt": "Set up the development environment",
                    "qa_prompt": "Verify environment is properly configured",
                },
                {
                    "title": "Create database schema",
                    "description": "Design and create DB schema",
                    "priority": "high",
                    "depends_on_indices": [0],
                    "worker_prompt": "Create the database schema",
                    "qa_prompt": "Verify schema matches design",
                },
            ],
        },
        {
            "name": "Phase 2: Implementation",
            "description": "Core implementation",
            "tasks": [
                {
                    "title": "Implement API",
                    "description": "Build the REST API",
                    "priority": "medium",
                    "depends_on_indices": [1],
                    "worker_prompt": "Implement the REST API endpoints",
                    "qa_prompt": "Verify API endpoints work correctly",
                },
            ],
        },
    ],
}


async def test_finalize_design_success(client: AsyncClient, db_session):
    """POST /api/architect/sessions/{id}/finalize{id} creates project with phases and tasks."""
    session_data = await create_session_via_api(client, db_session)
    session_id = session_data["id"]

    with patch("backend.src.api.architect.LLMClient") as MockClient:
        instance = MockClient.return_value
        instance.structured_output = AsyncMock(return_value=MOCK_FINALIZE_RESPONSE)

        response = await client.post(
            f"/api/v1/architect/sessions/{session_id}/finalize",
            json={"repo_path": "/test/repo"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "My Project"
    assert data["description"] == "A test project"
    assert data["repo_path"] == "/test/repo"
    assert data["status"] == "design"
    assert len(data["phases"]) == 2
    assert data["phases"][0]["name"] == "Phase 1: Setup"
    assert data["phases"][1]["name"] == "Phase 2: Implementation"

    # Verify session is project_bound
    get_resp = await client.get(f"/api/v1/architect/sessions/{session_id}")
    session = get_resp.json()
    assert session["status"] == "project_bound"
    assert session["project_id"] == data["id"]


async def test_finalize_assigns_worker_to_project(client: AsyncClient, db_session):
    """POST /api/architect/sessions/{id}/finalize assigns worker to project."""
    from sqlalchemy import select

    # Create a worker in DB
    worker_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    db_session.add(Worker(
        id=worker_id, name="test-worker", platform="linux", capabilities=[],
        executor_type="claude-code", status=WorkerStatus.idle,
        registered_at=now, last_heartbeat=now,
    ))
    await db_session.commit()

    # Create session with worker_id
    await insert_global_llm_settings(db_session)
    response = await client.post(
        "/api/v1/architect/sessions",
        json={"worker_id": str(worker_id)},
    )
    session_id = response.json()["id"]

    with patch("backend.src.api.architect.LLMClient") as MockClient:
        instance = MockClient.return_value
        instance.structured_output = AsyncMock(return_value=MOCK_FINALIZE_RESPONSE)

        response = await client.post(
            f"/api/v1/architect/sessions/{session_id}/finalize",
            json={"repo_path": "/test/repo"},
        )

    assert response.status_code == 200
    project_id = response.json()["id"]

    # Verify worker now has project_id assigned
    result = await db_session.execute(
        select(Worker).where(Worker.id == worker_id)
    )
    worker = result.scalar_one()
    assert str(worker.project_id) == project_id


async def test_finalize_design_with_pm_config(client: AsyncClient, db_session):
    """POST /api/architect/sessions/{id}/finalize{id} stores pm_llm_config in project."""
    session_data = await create_session_via_api(client, db_session)
    session_id = session_data["id"]

    with patch("backend.src.api.architect.LLMClient") as MockClient:
        instance = MockClient.return_value
        instance.structured_output = AsyncMock(return_value=MOCK_FINALIZE_RESPONSE)

        response = await client.post(
            f"/api/v1/architect/sessions/{session_id}/finalize",
            json={
                "repo_path": "/test/repo",
                "pm_llm_config": {"api_key": "sk-pm-key", "model": "gpt-4o"},
            },
        )

    assert response.status_code == 200
    data = response.json()
    # Architect config comes from global settings
    assert data["llm_config"]["architect"]["api_key"] == "sk-test-key-1234"
    assert data["llm_config"]["pm"]["api_key"] == "sk-pm-key"
    assert data["llm_config"]["pm"]["model"] == "gpt-4o"


async def test_finalize_session_not_found(client: AsyncClient, db_session):
    """POST /api/architect/sessions/{id}/finalize{random_id} returns 404."""
    random_id = str(uuid.uuid4())
    response = await client.post(
        f"/api/v1/architect/sessions/{random_id}/finalize",
        json={"repo_path": "/test/repo"},
    )
    assert response.status_code == 404


async def test_finalize_already_project_bound_returns_existing_project(
    client: AsyncClient, db_session
):
    """POST /api/architect/sessions/{id}/finalize on already project_bound session returns existing project."""
    session = await create_session_in_db(db_session)
    project, _phase = await create_project_with_phase(db_session)
    session.status = DesignSessionStatus.project_bound
    session.project_id = project.id
    await db_session.commit()

    response = await client.post(
        f"/api/v1/architect/sessions/{session.id}/finalize",
        json={"repo_path": "/test/repo"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(project.id)
    assert data["name"] == project.name


async def test_finalize_llm_error(client: AsyncClient, db_session):
    """POST /api/architect/sessions/{id}/finalize{id} returns 502 on LLM failure."""
    session_data = await create_session_via_api(client, db_session)
    session_id = session_data["id"]

    with patch("backend.src.api.architect.LLMClient") as MockClient:
        from backend.src.core.llm_client import LLMError

        instance = MockClient.return_value
        instance.structured_output = AsyncMock(side_effect=LLMError("API error"))

        response = await client.post(
            f"/api/v1/architect/sessions/{session_id}/finalize",
            json={"repo_path": "/test/repo"},
        )

    assert response.status_code == 502


async def test_finalize_creates_task_dependencies(client: AsyncClient, db_session):
    """POST /api/architect/sessions/{id}/finalize{id} correctly wires task dependencies."""
    session_data = await create_session_via_api(client, db_session)
    session_id = session_data["id"]

    with patch("backend.src.api.architect.LLMClient") as MockClient:
        instance = MockClient.return_value
        instance.structured_output = AsyncMock(return_value=MOCK_FINALIZE_RESPONSE)

        response = await client.post(
            f"/api/v1/architect/sessions/{session_id}/finalize",
            json={"repo_path": "/test/repo"},
        )

    assert response.status_code == 200
    data = response.json()
    project_id = data["id"]

    # Get all tasks for the project
    tasks_resp = await client.get(f"/api/v1/tasks/by-project/{project_id}")
    assert tasks_resp.status_code == 200
    tasks = tasks_resp.json()
    assert len(tasks) == 3


# ── Add Task Tests ───────────────────────────────────────────────────


MOCK_ADD_TASK_RESPONSE = {
    "title": "New Feature Task",
    "description": "Implement a new feature",
    "priority": "medium",
    "worker_prompt": "Implement the new feature following these steps...",
    "qa_prompt": "Verify the feature works correctly...",
}


async def test_add_task_success(client: AsyncClient, db_session):
    """POST /api/architect/add-task/{project_id} creates a task."""
    project, phase = await create_project_with_phase(db_session)

    with patch("backend.src.api.architect.LLMClient") as MockClient:
        instance = MockClient.return_value
        instance.structured_output = AsyncMock(return_value=MOCK_ADD_TASK_RESPONSE)

        response = await client.post(
            f"/api/v1/architect/add-task/{project.id}",
            json={
                "phase_id": str(phase.id),
                "request_text": "Add a user authentication feature",
            },
        )

    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "New Feature Task"
    assert data["description"] == "Implement a new feature"
    assert data["priority"] == "medium"
    assert data["worker_prompt"] == {
        "prompt": "Implement the new feature following these steps..."
    }
    assert data["qa_prompt"] == {"prompt": "Verify the feature works correctly..."}


async def test_add_task_with_llm_override(client: AsyncClient, db_session):
    """POST /api/architect/add-task/{project_id} uses override llm_config when provided."""
    project, phase = await create_project_with_phase(db_session)

    with patch("backend.src.api.architect.LLMClient") as MockClient:
        instance = MockClient.return_value
        instance.structured_output = AsyncMock(return_value=MOCK_ADD_TASK_RESPONSE)

        response = await client.post(
            f"/api/v1/architect/add-task/{project.id}",
            json={
                "phase_id": str(phase.id),
                "request_text": "Add a feature",
                "llm_config": {"api_key": "sk-override-key", "model": "claude-3-opus"},
            },
        )

    assert response.status_code == 201
    # Verify the client was created with the override config
    MockClient.assert_called_once()
    assert MockClient.call_args is not None
    call_args = MockClient.call_args[0][0]
    assert call_args.api_key == "sk-override-key"


async def test_add_task_project_not_found(client: AsyncClient, db_session):
    """POST /api/architect/add-task/{random_id} returns 404."""
    random_id = str(uuid.uuid4())
    random_phase_id = str(uuid.uuid4())
    response = await client.post(
        f"/api/v1/architect/add-task/{random_id}",
        json={
            "phase_id": random_phase_id,
            "request_text": "Add something",
        },
    )
    assert response.status_code == 404
    assert "Project not found" in response.json()["detail"]


async def test_add_task_phase_not_found(client: AsyncClient, db_session):
    """POST /api/architect/add-task/{project_id} with invalid phase returns 404."""
    project, _ = await create_project_with_phase(db_session)
    random_phase_id = str(uuid.uuid4())

    response = await client.post(
        f"/api/v1/architect/add-task/{project.id}",
        json={
            "phase_id": random_phase_id,
            "request_text": "Add something",
        },
    )
    assert response.status_code == 404
    assert "Phase not found" in response.json()["detail"]


async def test_add_task_phase_wrong_project(client: AsyncClient, db_session):
    """POST /api/architect/add-task/{project_id} with phase from another project returns 400."""
    project1, _ = await create_project_with_phase(db_session)

    # Create another project with its own phase
    now = datetime.now(timezone.utc)
    project2 = Project(
        id=uuid.uuid4(),
        name="Other Project",
        description="Other description",
        repo_path="/other/repo",
        status=ProjectStatus.active,
        llm_config={"architect": LLM_CONFIG},
        created_at=now,
        updated_at=now,
    )
    db_session.add(project2)
    phase2 = Phase(
        id=uuid.uuid4(),
        project_id=project2.id,
        name="Other Phase",
        branch_name="phase/other",
        order=1,
        status=PhaseStatus.active,
        created_at=now,
        updated_at=now,
    )
    db_session.add(phase2)
    await db_session.commit()

    response = await client.post(
        f"/api/v1/architect/add-task/{project1.id}",
        json={
            "phase_id": str(phase2.id),
            "request_text": "Add something",
        },
    )
    assert response.status_code == 400
    assert "does not belong" in response.json()["detail"]


async def test_add_task_no_llm_config(client: AsyncClient, db_session):
    """POST /api/architect/add-task/{project_id} without any LLM config returns 400."""
    now = datetime.now(timezone.utc)
    project = Project(
        id=uuid.uuid4(),
        name="No Config Project",
        description="No LLM config",
        repo_path="/test/repo",
        status=ProjectStatus.active,
        llm_config=None,
        created_at=now,
        updated_at=now,
    )
    db_session.add(project)
    phase = Phase(
        id=uuid.uuid4(),
        project_id=project.id,
        name="Phase 1",
        branch_name="phase/phase-1",
        order=1,
        status=PhaseStatus.active,
        created_at=now,
        updated_at=now,
    )
    db_session.add(phase)
    await db_session.commit()

    response = await client.post(
        f"/api/v1/architect/add-task/{project.id}",
        json={
            "phase_id": str(phase.id),
            "request_text": "Add something",
        },
    )
    assert response.status_code == 400
    assert "No LLM configuration" in response.json()["detail"]


async def test_add_task_llm_error(client: AsyncClient, db_session):
    """POST /api/architect/add-task/{project_id} returns 502 on LLM failure."""
    project, phase = await create_project_with_phase(db_session)

    with patch("backend.src.api.architect.LLMClient") as MockClient:
        from backend.src.core.llm_client import LLMError

        instance = MockClient.return_value
        instance.structured_output = AsyncMock(side_effect=LLMError("API error"))

        response = await client.post(
            f"/api/v1/architect/add-task/{project.id}",
            json={
                "phase_id": str(phase.id),
                "request_text": "Add something",
            },
        )

    assert response.status_code == 502


# ══════════════════════════════════════════════════════════════════════
# DIRECT UNIT TESTS — bypass ASGI transport for coverage.py tracing
# ══════════════════════════════════════════════════════════════════════


# ── _slugify ─────────────────────────────────────────────────────────


class TestSlugifyDirect:
    """Direct tests for the _slugify helper."""

    def test_simple_string(self):
        from backend.src.api.architect import _slugify

        assert _slugify("Hello World") == "hello-world"

    def test_special_characters(self):
        from backend.src.api.architect import _slugify

        assert _slugify("Phase 1: Setup & Config!") == "phase-1-setup-config"

    def test_unicode_characters(self):
        from backend.src.api.architect import _slugify

        assert _slugify("Deja vu") == "deja-vu"

    def test_multiple_spaces_and_dashes(self):
        from backend.src.api.architect import _slugify

        assert _slugify("hello   ---   world") == "hello-world"

    def test_leading_trailing_dashes(self):
        from backend.src.api.architect import _slugify

        assert _slugify("---hello---") == "hello"

    def test_empty_string(self):
        from backend.src.api.architect import _slugify

        assert _slugify("") == ""

    def test_only_special_characters(self):
        from backend.src.api.architect import _slugify

        assert _slugify("!@#$%") == ""

    def test_already_slug(self):
        from backend.src.api.architect import _slugify

        assert _slugify("already-a-slug") == "already-a-slug"

    def test_uppercase(self):
        from backend.src.api.architect import _slugify

        assert _slugify("UPPERCASE STRING") == "uppercase-string"

    def test_accented_characters(self):
        from backend.src.api.architect import _slugify

        result = _slugify("caf\u00e9 na\u00efve r\u00e9sum\u00e9")
        assert result == "cafe-naive-resume"


# ── _build_llm_config ────────────────────────────────────────────────


class TestBuildLLMConfigDirect:
    """Direct tests for the _build_llm_config helper."""

    def test_full_config(self):
        from backend.src.api.architect import _build_llm_config

        config = _build_llm_config(
            {
                "api_key": "sk-test",
                "model": "gpt-4o",
                "base_url": "https://custom.api",
            }
        )
        assert config.api_key == "sk-test"
        assert config.model == "gpt-4o"
        assert config.base_url == "https://custom.api"

    def test_minimal_config(self):
        from backend.src.api.architect import _build_llm_config

        config = _build_llm_config({"api_key": "sk-test"})
        assert config.api_key == "sk-test"
        assert config.model == "anthropic/claude-sonnet-4-20250514"
        assert config.base_url is None

    def test_empty_model_falls_back_to_default(self):
        from backend.src.api.architect import _build_llm_config

        config = _build_llm_config({"api_key": "sk-test", "model": ""})
        assert config.model == "anthropic/claude-sonnet-4-20250514"

    def test_none_model_falls_back_to_default(self):
        from backend.src.api.architect import _build_llm_config

        config = _build_llm_config({"api_key": "sk-test", "model": None})
        assert config.model == "anthropic/claude-sonnet-4-20250514"

    def test_base_url_none(self):
        from backend.src.api.architect import _build_llm_config

        config = _build_llm_config({"api_key": "sk-test", "base_url": None})
        assert config.base_url is None


# ── _build_message_history ───────────────────────────────────────────


class TestBuildMessageHistoryDirect:
    """Direct tests for the _build_message_history helper."""

    def test_with_messages(self):
        from backend.src.api.architect import _build_message_history

        now = datetime.now(timezone.utc)
        session = MagicMock()
        msg1 = MagicMock()
        msg1.role = MessageRole.assistant
        msg1.content = "Hello"
        msg1.created_at = now
        msg1.message_type = MessageType.chat

        msg2 = MagicMock()
        msg2.role = MessageRole.user
        msg2.content = "Help me"
        msg2.created_at = datetime(2099, 1, 2, tzinfo=timezone.utc)
        msg2.message_type = MessageType.chat

        session.messages = [msg2, msg1]  # intentionally reversed to test sorting

        with patch(
            "backend.src.api.architect.get_prompt", return_value="System prompt"
        ):
            result = _build_message_history(session)
        assert len(result) == 3
        # First should be system prompt, then sorted messages
        assert result[0] == {"role": "system", "content": "System prompt"}
        assert result[1] == {"role": "assistant", "content": "Hello"}
        assert result[2] == {"role": "user", "content": "Help me"}

    def test_with_empty_messages(self):
        from backend.src.api.architect import _build_message_history

        session = MagicMock()
        session.messages = []
        with patch(
            "backend.src.api.architect.get_prompt", return_value="System prompt"
        ):
            result = _build_message_history(session)
        assert len(result) == 1
        assert result[0] == {"role": "system", "content": "System prompt"}

    def test_with_string_role(self):
        from backend.src.api.architect import _build_message_history

        session = MagicMock()
        msg = MagicMock()
        msg.role = "user"  # plain string, no .value attr
        msg.content = "Test"
        msg.created_at = datetime.now(timezone.utc)
        msg.message_type = MessageType.chat
        session.messages = [msg]
        with patch(
            "backend.src.api.architect.get_prompt", return_value="System prompt"
        ):
            result = _build_message_history(session)
        assert result[0]["role"] == "system"
        assert result[1]["role"] == "user"

    def test_excludes_internal_messages(self):
        """Internal messages should be excluded from LLM message history."""
        from backend.src.api.architect import _build_message_history

        now = datetime.now(timezone.utc)
        session = MagicMock()

        chat_msg = MagicMock()
        chat_msg.role = MessageRole.user
        chat_msg.content = "Hello"
        chat_msg.created_at = now
        chat_msg.message_type = MessageType.chat

        internal_msg = MagicMock()
        internal_msg.role = MessageRole.user
        internal_msg.content = "finalize prompt"
        internal_msg.created_at = datetime(2099, 1, 1, tzinfo=timezone.utc)
        internal_msg.message_type = MessageType.internal

        session.messages = [chat_msg, internal_msg]

        with patch(
            "backend.src.api.architect.get_prompt", return_value="System prompt"
        ):
            result = _build_message_history(session)
        # Only system + chat_msg, internal excluded
        assert len(result) == 2
        assert result[0] == {"role": "system", "content": "System prompt"}
        assert result[1] == {"role": "user", "content": "Hello"}


# ── list_sessions (direct call) ──────────────────────────────────────


class TestListSessionsDirect:
    """Direct tests for list_sessions endpoint function."""

    async def test_list_empty(self, db_session):
        from backend.src.api.architect import list_sessions

        result = await list_sessions(status=None, db=db_session)
        assert result == []

    async def test_list_returns_sessions(self, db_session):
        from backend.src.api.architect import list_sessions

        await create_session_in_db(db_session)
        await create_session_in_db(db_session)
        result = await list_sessions(status=None, db=db_session)
        assert len(result) == 2

    async def test_list_filter_active(self, db_session):
        from backend.src.api.architect import list_sessions

        _ = await create_session_in_db(db_session)
        session2 = await create_session_in_db(db_session)
        session2.status = DesignSessionStatus.project_bound
        await db_session.commit()
        result = await list_sessions(status="active", db=db_session)
        assert all(r.status.value == "active" for r in result)

    async def test_list_filter_project_bound(self, db_session):
        from backend.src.api.architect import list_sessions

        session = await create_session_in_db(db_session)
        session.status = DesignSessionStatus.project_bound
        await db_session.commit()
        result = await list_sessions(status="project_bound", db=db_session)
        assert len(result) == 1
        assert result[0].status.value == "project_bound"

    async def test_list_invalid_status(self, db_session):
        from backend.src.api.architect import list_sessions

        with pytest.raises(HTTPException) as exc_info:
            await list_sessions(status="invalid", db=db_session)
        assert exc_info.value.status_code == 400


# ── create_session (direct call) ─────────────────────────────────────


class TestCreateSessionDirect:
    """Direct tests for create_session endpoint function."""

    async def test_create_session_with_full_global_settings(self, db_session):
        from backend.src import schemas
        from backend.src.api.architect import create_session

        await insert_global_llm_settings(
            db_session, api_key="sk-direct", model="gpt-4o", base_url="https://api.test"
        )
        body = schemas.CreateSessionRequest()
        result = await create_session(body=body, db=db_session)
        assert result.status == schemas.DesignSessionStatus.active
        assert result.project_id is None
        assert len(result.messages) == 0

    async def test_create_session_minimal_global_settings(self, db_session):
        from backend.src import schemas
        from backend.src.api.architect import create_session

        await insert_global_llm_settings(db_session, api_key="sk-min", model="")
        body = schemas.CreateSessionRequest()
        result = await create_session(body=body, db=db_session)
        assert result.status == schemas.DesignSessionStatus.active
        assert len(result.messages) == 0

    async def test_create_session_missing_global_api_key(self, db_session):
        from backend.src import schemas
        from backend.src.api.architect import create_session

        body = schemas.CreateSessionRequest()
        with pytest.raises(HTTPException) as exc_info:
            await create_session(body=body, db=db_session)
        assert exc_info.value.status_code == 400
        assert "LLM API key not configured" in exc_info.value.detail


# ── get_session (direct call) ────────────────────────────────────────


class TestGetSessionDirect:
    """Direct tests for get_session endpoint function."""

    async def test_get_existing_session(self, db_session):
        from backend.src.api.architect import get_session

        session = await create_session_in_db(db_session)
        result = await get_session(session_id=session.id, db=db_session)
        assert result.id == session.id
        assert result.status.value == "active"

    async def test_get_nonexistent_session(self, db_session):
        from backend.src.api.architect import get_session

        with pytest.raises(HTTPException) as exc_info:
            await get_session(session_id=uuid.uuid4(), db=db_session)
        assert exc_info.value.status_code == 404


# ── Internal message filtering tests ─────────────────────────────


class TestInternalMessageFiltering:
    """Tests that internal messages are excluded from API responses."""

    async def test_get_session_excludes_internal_messages(self, db_session):
        """get_session should not include internal messages in the response."""
        from backend.src.api.architect import get_session
        from backend.src.repositories.design_session_repository import (
            DesignSessionRepository,
        )

        session = await create_session_in_db(db_session)
        repo = DesignSessionRepository(db_session)
        await repo.add_message(
            session.id, MessageRole.user, "Hello", message_type=MessageType.chat
        )
        await repo.add_message(
            session.id, MessageRole.assistant, "Hi there", message_type=MessageType.chat
        )
        await repo.add_message(
            session.id,
            MessageRole.user,
            "finalize prompt",
            message_type=MessageType.internal,
        )
        await repo.add_message(
            session.id,
            MessageRole.assistant,
            '{"phases": []}',
            message_type=MessageType.internal,
        )
        await repo.commit()

        result = await get_session(session_id=session.id, db=db_session)
        assert len(result.messages) == 2
        assert all(m.content != "finalize prompt" for m in result.messages)

    async def test_list_sessions_excludes_internal_messages(self, db_session):
        """list_sessions should not include internal messages in any session."""
        from backend.src.api.architect import list_sessions
        from backend.src.repositories.design_session_repository import (
            DesignSessionRepository,
        )

        session = await create_session_in_db(db_session)
        repo = DesignSessionRepository(db_session)
        await repo.add_message(
            session.id, MessageRole.user, "Hello", message_type=MessageType.chat
        )
        await repo.add_message(
            session.id,
            MessageRole.assistant,
            "internal response",
            message_type=MessageType.internal,
        )
        await repo.commit()

        result = await list_sessions(status=None, db=db_session)
        target = [s for s in result if s.id == session.id][0]
        assert len(target.messages) == 1
        assert target.messages[0].content == "Hello"

    async def test_finalize_stores_internal_messages(self, db_session):
        """finalize_design should store finalize prompt/response as internal messages."""
        from backend.src import schemas
        from backend.src.api.architect import finalize_design
        from backend.src.models import DesignMessage
        from sqlalchemy import select

        session = await create_session_in_db(db_session)
        body = schemas.FinalizeRequest(repo_path="/test/repo")

        with patch("backend.src.api.architect.LLMClient") as MockClient:
            instance = MockClient.return_value
            instance.structured_output = AsyncMock(return_value=MOCK_FINALIZE_RESPONSE)

            await finalize_design(session_id=session.id, body=body, db=db_session)

        # Query internal messages directly to bypass identity map caching
        result = await db_session.execute(
            select(DesignMessage)
            .where(DesignMessage.session_id == session.id)
            .where(DesignMessage.message_type == MessageType.internal)
            .order_by(DesignMessage.created_at)
        )
        internal_msgs = list(result.scalars().all())
        assert len(internal_msgs) == 2
        # First internal: finalize prompt (user), second: LLM JSON response (assistant)
        assert internal_msgs[0].role == MessageRole.user
        assert internal_msgs[1].role == MessageRole.assistant


# ── send_message (direct call) ───────────────────────────────────────


class TestSendMessageDirect:
    """Direct tests for send_message endpoint function."""

    async def test_send_message_success(self, db_session):
        from backend.src import schemas
        from backend.src.api.architect import send_message

        session = await create_session_in_db(db_session)
        body = schemas.MessageRequest(content="Help me design an API")

        with patch("backend.src.api.architect.LLMClient") as MockClient:
            instance = MockClient.return_value
            instance.chat = AsyncMock(return_value="Sure, I can help!")

            result = await send_message(session_id=session.id, body=body, db=db_session)

        assert result.role == schemas.MessageRole.assistant
        assert result.content == "Sure, I can help!"
        assert result.session_id == session.id

    async def test_send_message_cancelled_session(self, db_session):
        from backend.src import schemas
        from backend.src.api.architect import send_message

        session = await create_session_in_db(db_session)
        session.status = DesignSessionStatus.cancelled
        await db_session.commit()

        body = schemas.MessageRequest(content="Hello")

        with pytest.raises(HTTPException) as exc_info:
            await send_message(session_id=session.id, body=body, db=db_session)
        assert exc_info.value.status_code == 400
        assert "cancelled" in exc_info.value.detail

    async def test_send_message_session_not_found(self, db_session):
        from backend.src import schemas
        from backend.src.api.architect import send_message

        body = schemas.MessageRequest(content="Hello")
        with pytest.raises(HTTPException) as exc_info:
            await send_message(session_id=uuid.uuid4(), body=body, db=db_session)
        assert exc_info.value.status_code == 404

    async def test_send_message_llm_error(self, db_session):
        from backend.src import schemas
        from backend.src.api.architect import send_message
        from backend.src.core.llm_client import LLMError

        session = await create_session_in_db(db_session)
        body = schemas.MessageRequest(content="Hello")

        with patch("backend.src.api.architect.LLMClient") as MockClient:
            instance = MockClient.return_value
            instance.chat = AsyncMock(side_effect=LLMError("Rate limited"))

            with pytest.raises(HTTPException) as exc_info:
                await send_message(session_id=session.id, body=body, db=db_session)
            assert exc_info.value.status_code == 502
            assert "LLM error" in exc_info.value.detail


# ── send_message_stream (direct call) ────────────────────────────────


class TestSendMessageStreamDirect:
    """Direct tests for send_message_stream endpoint function."""

    async def test_stream_message_returns_event_source_response(self, db_session):
        from backend.src import schemas
        from backend.src.api.architect import send_message_stream

        session = await create_session_in_db(db_session)
        body = schemas.MessageRequest(content="Hello")

        async def mock_stream_chat(messages, **kwargs):
            for chunk in ["Hello", " world"]:
                yield chunk

        with patch("backend.src.api.architect.LLMClient") as MockClient:
            instance = MockClient.return_value
            instance.stream_chat = mock_stream_chat

            result = await send_message_stream(
                session_id=session.id, body=body, db=db_session
            )

        # EventSourceResponse is returned
        from sse_starlette.sse import EventSourceResponse

        assert isinstance(result, EventSourceResponse)

    async def test_stream_message_cancelled_session(self, db_session):
        from backend.src import schemas
        from backend.src.api.architect import send_message_stream

        session = await create_session_in_db(db_session)
        session.status = DesignSessionStatus.cancelled
        await db_session.commit()

        body = schemas.MessageRequest(content="Hello")

        with pytest.raises(HTTPException) as exc_info:
            await send_message_stream(session_id=session.id, body=body, db=db_session)
        assert exc_info.value.status_code == 400

    async def test_stream_message_session_not_found(self, db_session):
        from backend.src import schemas
        from backend.src.api.architect import send_message_stream

        body = schemas.MessageRequest(content="Hello")
        with pytest.raises(HTTPException) as exc_info:
            await send_message_stream(session_id=uuid.uuid4(), body=body, db=db_session)
        assert exc_info.value.status_code == 404

    async def test_stream_event_generator_chunks(self, db_session):
        """Test the event generator yields chunk and done events."""
        from backend.src import schemas
        from backend.src.api.architect import send_message_stream

        session = await create_session_in_db(db_session)
        body = schemas.MessageRequest(content="Hello")

        chunks_received = []

        async def mock_stream_chat(messages, **kwargs):
            for chunk in ["A", "B", "C"]:
                yield chunk

        with patch("backend.src.api.architect.LLMClient") as MockClient:
            instance = MockClient.return_value
            instance.stream_chat = mock_stream_chat

            sse_response = await send_message_stream(
                session_id=session.id, body=body, db=db_session
            )

            # Iterate the generator to collect events
            async for event in sse_response.body_iterator:
                chunks_received.append(event)

        # The generator should have produced chunk events and a done event
        assert len(chunks_received) > 0

    async def test_stream_event_generator_llm_error(self, db_session):
        """Test the event generator handles LLM errors gracefully."""
        from backend.src import schemas
        from backend.src.api.architect import send_message_stream
        from backend.src.core.llm_client import LLMError

        session = await create_session_in_db(db_session)
        body = schemas.MessageRequest(content="Hello")

        async def mock_stream_chat_error(messages, **kwargs):
            raise LLMError("Stream failed")
            yield  # make it a generator  # noqa: E501

        with patch("backend.src.api.architect.LLMClient") as MockClient:
            instance = MockClient.return_value
            instance.stream_chat = mock_stream_chat_error

            sse_response = await send_message_stream(
                session_id=session.id, body=body, db=db_session
            )

            events = []
            async for event in sse_response.body_iterator:
                events.append(event)

        # Should have yielded an error event
        assert len(events) > 0


# ── finalize_design (direct call) ────────────────────────────────────


class TestFinalizeDesignDirect:
    """Direct tests for finalize_design endpoint function."""

    async def test_finalize_success(self, db_session):
        from backend.src import schemas
        from backend.src.api.architect import finalize_design

        session = await create_session_in_db(db_session)
        body = schemas.FinalizeRequest(repo_path="/test/repo")

        with patch("backend.src.api.architect.LLMClient") as MockClient:
            instance = MockClient.return_value
            instance.structured_output = AsyncMock(return_value=MOCK_FINALIZE_RESPONSE)

            result = await finalize_design(
                session_id=session.id, body=body, db=db_session
            )

        assert result.name == "My Project"
        assert result.description == "A test project"
        assert result.repo_path == "/test/repo"
        assert result.status == schemas.ProjectStatus.design
        assert len(result.phases) == 2

    async def test_finalize_with_pm_config(self, db_session):
        from backend.src import schemas
        from backend.src.api.architect import finalize_design

        session = await create_session_in_db(db_session)
        body = schemas.FinalizeRequest(
            repo_path="/test/repo",
            pm_llm_config=schemas.LLMConfigInput(
                api_key="sk-pm", model="gpt-4o", base_url="https://pm.api"
            ),
        )

        with patch("backend.src.api.architect.LLMClient") as MockClient:
            instance = MockClient.return_value
            instance.structured_output = AsyncMock(return_value=MOCK_FINALIZE_RESPONSE)

            result = await finalize_design(
                session_id=session.id, body=body, db=db_session
            )

        assert result.llm_config is not None
        assert result.llm_config["pm"]["api_key"] == "sk-pm"
        assert result.llm_config["pm"]["model"] == "gpt-4o"
        assert result.llm_config["pm"]["base_url"] == "https://pm.api"

    async def test_finalize_project_bound_session_returns_existing_project(
        self, db_session
    ):
        from backend.src import schemas
        from backend.src.api.architect import finalize_design

        session = await create_session_in_db(db_session)
        project, _phase = await create_project_with_phase(db_session)
        session.status = DesignSessionStatus.project_bound
        session.project_id = project.id
        await db_session.commit()

        body = schemas.FinalizeRequest(repo_path="/test/repo")
        result = await finalize_design(session_id=session.id, body=body, db=db_session)
        assert result.id == project.id
        assert result.name == project.name

    async def test_finalize_session_not_found(self, db_session):
        from backend.src import schemas
        from backend.src.api.architect import finalize_design

        body = schemas.FinalizeRequest(repo_path="/test/repo")
        with pytest.raises(HTTPException) as exc_info:
            await finalize_design(session_id=uuid.uuid4(), body=body, db=db_session)
        assert exc_info.value.status_code == 404

    async def test_finalize_llm_error(self, db_session):
        from backend.src import schemas
        from backend.src.api.architect import finalize_design
        from backend.src.core.llm_client import LLMError

        session = await create_session_in_db(db_session)
        body = schemas.FinalizeRequest(repo_path="/test/repo")

        with patch("backend.src.api.architect.LLMClient") as MockClient:
            instance = MockClient.return_value
            instance.structured_output = AsyncMock(side_effect=LLMError("API error"))

            with pytest.raises(HTTPException) as exc_info:
                await finalize_design(session_id=session.id, body=body, db=db_session)
            assert exc_info.value.status_code == 502

    async def test_finalize_with_invalid_priority(self, db_session):
        """Finalize handles unknown priority gracefully (falls back to medium)."""
        from backend.src import schemas
        from backend.src.api.architect import finalize_design

        session = await create_session_in_db(db_session)
        body = schemas.FinalizeRequest(repo_path="/test/repo")

        response_with_bad_priority = {
            "project_name": "Bad Priority Project",
            "project_description": "Test",
            "phases": [
                {
                    "name": "Phase 1",
                    "description": "Test phase",
                    "tasks": [
                        {
                            "title": "Task with bad priority",
                            "description": "Test",
                            "priority": "ultra-mega-critical",
                            "depends_on_indices": [],
                            "worker_prompt": "Do something",
                            "qa_prompt": "Check something",
                        }
                    ],
                }
            ],
        }

        with patch("backend.src.api.architect.LLMClient") as MockClient:
            instance = MockClient.return_value
            instance.structured_output = AsyncMock(
                return_value=response_with_bad_priority
            )

            result = await finalize_design(
                session_id=session.id, body=body, db=db_session
            )

        assert result.name == "Bad Priority Project"
        assert len(result.phases) == 1

    async def test_finalize_with_pm_config_no_model_no_base_url(self, db_session):
        """Finalize with pm_llm_config that has no model or base_url."""
        from backend.src import schemas
        from backend.src.api.architect import finalize_design

        session = await create_session_in_db(db_session)
        body = schemas.FinalizeRequest(
            repo_path="/test/repo",
            pm_llm_config=schemas.LLMConfigInput(api_key="sk-pm-only"),
        )

        with patch("backend.src.api.architect.LLMClient") as MockClient:
            instance = MockClient.return_value
            instance.structured_output = AsyncMock(return_value=MOCK_FINALIZE_RESPONSE)

            result = await finalize_design(
                session_id=session.id, body=body, db=db_session
            )

        assert result.llm_config is not None
        assert result.llm_config["pm"]["api_key"] == "sk-pm-only"
        assert "model" not in result.llm_config["pm"]
        assert "base_url" not in result.llm_config["pm"]

    async def test_finalize_empty_phases(self, db_session):
        """Finalize with empty phases list raises 400."""
        from backend.src import schemas
        from backend.src.api.architect import finalize_design

        session = await create_session_in_db(db_session)
        body = schemas.FinalizeRequest(repo_path="/test/repo")

        empty_response = {
            "project_name": "Empty Project",
            "project_description": "No phases",
            "phases": [],
        }

        with patch("backend.src.api.architect.LLMClient") as MockClient:
            instance = MockClient.return_value
            instance.structured_output = AsyncMock(return_value=empty_response)

            with pytest.raises(HTTPException) as exc_info:
                await finalize_design(
                    session_id=session.id, body=body, db=db_session
                )
            assert exc_info.value.status_code == 400
            assert "at least one phase" in exc_info.value.detail

    async def test_finalize_with_out_of_range_dependency_index(self, db_session):
        """Finalize ignores out-of-range depends_on_indices."""
        from backend.src import schemas
        from backend.src.api.architect import finalize_design

        session = await create_session_in_db(db_session)
        body = schemas.FinalizeRequest(repo_path="/test/repo")

        response_with_bad_dep = {
            "project_name": "Bad Dep Project",
            "project_description": "Test",
            "phases": [
                {
                    "name": "Phase 1",
                    "description": "Test",
                    "tasks": [
                        {
                            "title": "Task 1",
                            "description": "First task",
                            "priority": "high",
                            "depends_on_indices": [99],  # out of range
                            "worker_prompt": "Do something",
                            "qa_prompt": "Check something",
                        }
                    ],
                }
            ],
        }

        with patch("backend.src.api.architect.LLMClient") as MockClient:
            instance = MockClient.return_value
            instance.structured_output = AsyncMock(return_value=response_with_bad_dep)

            result = await finalize_design(
                session_id=session.id, body=body, db=db_session
            )

        assert result.name == "Bad Dep Project"
        assert len(result.phases) == 1

    async def test_finalize_uses_design_context_when_available(self, db_session):
        """finalize_design uses optimized path (no full history) when design_context exists."""
        from backend.src import schemas
        from backend.src.api.architect import finalize_design
        from backend.src.repositories.design_session_repository import (
            DesignSessionRepository,
        )

        session = await create_session_in_db(db_session)
        repo = DesignSessionRepository(db_session)

        # Add chat messages including one with design_context
        await repo.add_message(
            session.id,
            MessageRole.user,
            "Build me a SaaS",
            message_type=MessageType.chat,
        )
        await repo.add_message(
            session.id,
            MessageRole.assistant,
            "Sure!\n<design_context>\nProject: Test SaaS\nStack: FastAPI + React\n</design_context>",
            message_type=MessageType.chat,
        )
        await repo.commit()

        body = schemas.FinalizeRequest(repo_path="/test/repo")
        captured_messages = None

        with patch("backend.src.api.architect.LLMClient") as MockClient:
            instance = MockClient.return_value

            async def capture_structured_output(messages, **kwargs):
                nonlocal captured_messages
                captured_messages = messages
                return MOCK_FINALIZE_RESPONSE

            instance.structured_output = AsyncMock(
                side_effect=capture_structured_output
            )

            await finalize_design(session_id=session.id, body=body, db=db_session)

        # Optimized path: only system + user (finalize prompt with design_context)
        assert captured_messages is not None
        assert len(captured_messages) == 2
        assert captured_messages[0]["role"] == "system"
        assert captured_messages[1]["role"] == "user"
        assert "Project: Test SaaS" in captured_messages[1]["content"]
        assert "Stack: FastAPI + React" in captured_messages[1]["content"]

    async def test_finalize_falls_back_to_full_history_without_design_context(
        self, db_session
    ):
        """finalize_design uses full conversation history when no design_context."""
        from backend.src import schemas
        from backend.src.api.architect import finalize_design
        from backend.src.repositories.design_session_repository import (
            DesignSessionRepository,
        )

        session = await create_session_in_db(db_session)
        repo = DesignSessionRepository(db_session)

        # Add chat messages WITHOUT design_context
        await repo.add_message(
            session.id,
            MessageRole.user,
            "Build me a SaaS",
            message_type=MessageType.chat,
        )
        await repo.add_message(
            session.id,
            MessageRole.assistant,
            "Sure, let me help!",
            message_type=MessageType.chat,
        )
        await repo.commit()

        body = schemas.FinalizeRequest(repo_path="/test/repo")
        captured_messages = None

        with patch("backend.src.api.architect.LLMClient") as MockClient:
            instance = MockClient.return_value

            async def capture_structured_output(messages, **kwargs):
                nonlocal captured_messages
                captured_messages = messages
                return MOCK_FINALIZE_RESPONSE

            instance.structured_output = AsyncMock(
                side_effect=capture_structured_output
            )

            await finalize_design(session_id=session.id, body=body, db=db_session)

        # Fallback path: system + full history + finalize prompt
        assert captured_messages is not None
        assert len(captured_messages) >= 4  # system + user + assistant + finalize user
        assert captured_messages[0]["role"] == "system"
        assert captured_messages[-1]["role"] == "user"
        assert "(see conversation history above)" in captured_messages[-1]["content"]

    async def test_finalize_with_missing_fields_in_response(self, db_session):
        """Finalize uses defaults when fields are missing from LLM response."""
        from backend.src import schemas
        from backend.src.api.architect import finalize_design

        session = await create_session_in_db(db_session)
        body = schemas.FinalizeRequest(repo_path="/test/repo")

        minimal_response = {
            "phases": [
                {
                    "tasks": [
                        {
                            "depends_on_indices": [],
                        }
                    ],
                }
            ],
        }

        with patch("backend.src.api.architect.LLMClient") as MockClient:
            instance = MockClient.return_value
            instance.structured_output = AsyncMock(return_value=minimal_response)

            result = await finalize_design(
                session_id=session.id, body=body, db=db_session
            )

        assert result.name == "Untitled Project"
        assert result.description == ""


# ── add_task (direct call) ───────────────────────────────────────────


class TestAddTaskDirect:
    """Direct tests for add_task endpoint function."""

    async def test_add_task_success(self, db_session):
        from backend.src import schemas
        from backend.src.api.architect import add_task

        project, phase = await create_project_with_phase(db_session)
        body = schemas.AddTaskRequest(
            phase_id=phase.id,
            request_text="Add a user authentication feature",
        )

        with patch("backend.src.api.architect.LLMClient") as MockClient:
            instance = MockClient.return_value
            instance.structured_output = AsyncMock(return_value=MOCK_ADD_TASK_RESPONSE)

            result = await add_task(project_id=project.id, body=body, db=db_session)

        assert result.title == "New Feature Task"
        assert result.description == "Implement a new feature"
        assert result.priority == schemas.TaskPriority.medium

    async def test_add_task_with_llm_override(self, db_session):
        from backend.src import schemas
        from backend.src.api.architect import add_task

        project, phase = await create_project_with_phase(db_session)
        body = schemas.AddTaskRequest(
            phase_id=phase.id,
            request_text="Add a feature",
            llm_config=schemas.LLMConfigInput(
                api_key="sk-override", model="gpt-4o", base_url="https://override.api"
            ),
        )

        with patch("backend.src.api.architect.LLMClient") as MockClient:
            instance = MockClient.return_value
            instance.structured_output = AsyncMock(return_value=MOCK_ADD_TASK_RESPONSE)

            result = await add_task(project_id=project.id, body=body, db=db_session)

        assert result.title == "New Feature Task"
        # Verify the config was created with override values
        assert MockClient.call_args is not None
        call_args = MockClient.call_args[0][0]
        assert call_args.api_key == "sk-override"
        assert call_args.model == "gpt-4o"
        assert call_args.base_url == "https://override.api"

    async def test_add_task_project_not_found(self, db_session):
        from backend.src import schemas
        from backend.src.api.architect import add_task

        body = schemas.AddTaskRequest(
            phase_id=uuid.uuid4(),
            request_text="Add something",
        )

        with pytest.raises(HTTPException) as exc_info:
            await add_task(project_id=uuid.uuid4(), body=body, db=db_session)
        assert exc_info.value.status_code == 404
        assert "Project not found" in exc_info.value.detail

    async def test_add_task_phase_not_found(self, db_session):
        from backend.src import schemas
        from backend.src.api.architect import add_task

        project, _ = await create_project_with_phase(db_session)
        body = schemas.AddTaskRequest(
            phase_id=uuid.uuid4(),
            request_text="Add something",
        )

        with pytest.raises(HTTPException) as exc_info:
            await add_task(project_id=project.id, body=body, db=db_session)
        assert exc_info.value.status_code == 404
        assert "Phase not found" in exc_info.value.detail

    async def test_add_task_phase_wrong_project(self, db_session):
        from backend.src import schemas
        from backend.src.api.architect import add_task

        project1, _ = await create_project_with_phase(db_session)
        _, phase2 = await create_project_with_phase(db_session)

        body = schemas.AddTaskRequest(
            phase_id=phase2.id,
            request_text="Add something",
        )

        with pytest.raises(HTTPException) as exc_info:
            await add_task(project_id=project1.id, body=body, db=db_session)
        assert exc_info.value.status_code == 400
        assert "does not belong" in exc_info.value.detail

    async def test_add_task_no_llm_config(self, db_session):
        from backend.src import schemas
        from backend.src.api.architect import add_task

        now = datetime.now(timezone.utc)
        project = Project(
            id=uuid.uuid4(),
            name="No Config",
            description="No LLM config",
            repo_path="/test/repo",
            status=ProjectStatus.active,
            llm_config=None,
            created_at=now,
            updated_at=now,
        )
        db_session.add(project)
        phase = Phase(
            id=uuid.uuid4(),
            project_id=project.id,
            name="Phase 1",
            branch_name="phase/phase-1",
            order=1,
            status=PhaseStatus.active,
            created_at=now,
            updated_at=now,
        )
        db_session.add(phase)
        await db_session.commit()

        body = schemas.AddTaskRequest(
            phase_id=phase.id,
            request_text="Add something",
        )

        with pytest.raises(HTTPException) as exc_info:
            await add_task(project_id=project.id, body=body, db=db_session)
        assert exc_info.value.status_code == 400
        assert "No LLM configuration" in exc_info.value.detail

    async def test_add_task_llm_error(self, db_session):
        from backend.src import schemas
        from backend.src.api.architect import add_task
        from backend.src.core.llm_client import LLMError

        project, phase = await create_project_with_phase(db_session)
        body = schemas.AddTaskRequest(
            phase_id=phase.id,
            request_text="Add something",
        )

        with patch("backend.src.api.architect.LLMClient") as MockClient:
            instance = MockClient.return_value
            instance.structured_output = AsyncMock(side_effect=LLMError("API error"))

            with pytest.raises(HTTPException) as exc_info:
                await add_task(project_id=project.id, body=body, db=db_session)
            assert exc_info.value.status_code == 502

    async def test_add_task_with_invalid_priority(self, db_session):
        """add_task handles unknown priority from LLM (falls back to medium)."""
        from backend.src import schemas
        from backend.src.api.architect import add_task

        project, phase = await create_project_with_phase(db_session)
        body = schemas.AddTaskRequest(
            phase_id=phase.id,
            request_text="Add a feature",
        )

        bad_priority_response = {
            "title": "Task with bad priority",
            "description": "Test",
            "priority": "not-a-real-priority",
            "worker_prompt": "Do something",
            "qa_prompt": "Check something",
        }

        with patch("backend.src.api.architect.LLMClient") as MockClient:
            instance = MockClient.return_value
            instance.structured_output = AsyncMock(return_value=bad_priority_response)

            result = await add_task(project_id=project.id, body=body, db=db_session)

        assert result.priority == schemas.TaskPriority.medium

    async def test_add_task_uses_project_llm_config(self, db_session):
        """add_task falls back to project architect LLM config when no override."""
        from backend.src import schemas
        from backend.src.api.architect import add_task

        project, phase = await create_project_with_phase(db_session)
        body = schemas.AddTaskRequest(
            phase_id=phase.id,
            request_text="Add a feature",
            # No llm_config override, so it uses project.llm_config["architect"]
        )

        with patch("backend.src.api.architect.LLMClient") as MockClient:
            instance = MockClient.return_value
            instance.structured_output = AsyncMock(return_value=MOCK_ADD_TASK_RESPONSE)

            result = await add_task(project_id=project.id, body=body, db=db_session)

        assert result.title == "New Feature Task"
        # Should have used the project's architect config
        assert MockClient.call_args is not None
        call_args = MockClient.call_args[0][0]
        assert call_args.api_key == "sk-test-key-1234"

    async def test_add_task_llm_config_without_api_key(self, db_session):
        """add_task fails when project llm_config has architect but no api_key."""
        from backend.src import schemas
        from backend.src.api.architect import add_task

        now = datetime.now(timezone.utc)
        project = Project(
            id=uuid.uuid4(),
            name="No API Key Project",
            description="Config without api_key",
            repo_path="/test/repo",
            status=ProjectStatus.active,
            llm_config={"architect": {"model": "gpt-4o"}},  # no api_key
            created_at=now,
            updated_at=now,
        )
        db_session.add(project)
        phase = Phase(
            id=uuid.uuid4(),
            project_id=project.id,
            name="Phase 1",
            branch_name="phase/phase-1",
            order=1,
            status=PhaseStatus.active,
            created_at=now,
            updated_at=now,
        )
        db_session.add(phase)
        await db_session.commit()

        body = schemas.AddTaskRequest(
            phase_id=phase.id,
            request_text="Add something",
        )

        with pytest.raises(HTTPException) as exc_info:
            await add_task(project_id=project.id, body=body, db=db_session)
        assert exc_info.value.status_code == 400
        assert "No LLM configuration" in exc_info.value.detail


# ── _find_potential_marker_start unit tests ──────────────────────────


class TestFindPotentialMarkerStart:
    """Direct unit tests for _find_potential_marker_start helper."""

    def test_no_marker_in_normal_text(self):
        from backend.src.api.architect import _find_potential_marker_start

        assert _find_potential_marker_start("Hello world") is None

    def test_partial_design_context_tag(self):
        from backend.src.api.architect import _find_potential_marker_start

        result = _find_potential_marker_start("some text<des")
        assert result is not None
        assert "some text<des"[result:] == "<des"

    def test_partial_finalize_marker(self):
        from backend.src.api.architect import _find_potential_marker_start

        result = _find_potential_marker_start("some text[FIN")
        assert result is not None
        assert "some text[FIN"[result:] == "[FIN"

    def test_full_design_context_tag_detected(self):
        from backend.src.api.architect import _find_potential_marker_start

        result = _find_potential_marker_start("text<design_context>")
        assert result is not None
        assert "text<design_context>"[result:] == "<design_context>"

    def test_full_finalize_marker_detected(self):
        from backend.src.api.architect import _find_potential_marker_start

        result = _find_potential_marker_start("text[FINALIZE]")
        assert result is not None
        assert "text[FINALIZE]"[result:] == "[FINALIZE]"

    def test_single_angle_bracket(self):
        from backend.src.api.architect import _find_potential_marker_start

        result = _find_potential_marker_start("some text<")
        assert result is not None
        assert "some text<"[result:] == "<"

    def test_single_open_bracket(self):
        from backend.src.api.architect import _find_potential_marker_start

        result = _find_potential_marker_start("some text[")
        assert result is not None
        assert "some text["[result:] == "["

    def test_empty_string(self):
        from backend.src.api.architect import _find_potential_marker_start

        assert _find_potential_marker_start("") is None

    def test_non_marker_angle_bracket_completed(self):
        """A completed non-marker tag like <div> should not trigger detection."""
        from backend.src.api.architect import _find_potential_marker_start

        assert _find_potential_marker_start("text<div>") is None

    def test_non_marker_bracket_completed(self):
        from backend.src.api.architect import _find_potential_marker_start

        assert _find_potential_marker_start("[FOO]") is None

    def test_design_context_one_char_at_a_time(self):
        """Progressively build '<design_context>' and verify detection at each step."""
        from backend.src.api.architect import _find_potential_marker_start

        marker = "<design_context>"
        for i in range(1, len(marker) + 1):
            text = "prefix" + marker[:i]
            result = _find_potential_marker_start(text)
            assert result is not None, f"Failed to detect prefix of length {i}: {marker[:i]}"
            assert text[result:] == marker[:i]


# ── redesign_phase (direct call) ─────────────────────────────────────


class TestRedesignPhaseDirect:
    """Direct tests for redesign_phase endpoint function."""

    async def _make_phase_with_tasks(self, db_session, *, redesign_count=1, done_count=0):
        """Helper: create project + phase + tasks in redesign/done status."""
        from backend.src.models import Task, TaskStatus, TaskPriority

        project, phase = await create_project_with_phase(db_session)

        redesign_tasks = []
        for i in range(redesign_count):
            t = Task(
                id=uuid.uuid4(),
                project_id=project.id,
                phase_id=phase.id,
                title=f"Redesign Task {i+1}",
                description=f"Failed task {i+1}",
                status=TaskStatus.redesign,
                priority=TaskPriority.high,
                worker_prompt={"prompt": f"Do thing {i+1}"},
                qa_prompt={"prompt": f"Check thing {i+1}"},
                error_message=f"Error in task {i+1}",
                retry_count=3,
                max_retries=3,
                qa_feedback_history=[{"round": 1, "feedback": "bad"}],
                branch_name=phase.branch_name,
            )
            db_session.add(t)
            redesign_tasks.append(t)

        done_tasks = []
        for i in range(done_count):
            t = Task(
                id=uuid.uuid4(),
                project_id=project.id,
                phase_id=phase.id,
                title=f"Done Task {i+1}",
                description=f"Completed task {i+1}",
                status=TaskStatus.done,
                priority=TaskPriority.medium,
                branch_name=phase.branch_name,
            )
            db_session.add(t)
            done_tasks.append(t)

        await db_session.commit()
        return project, phase, redesign_tasks, done_tasks

    async def test_redesign_phase_success(self, db_session):
        """redesign_phase keeps, deletes, and creates tasks correctly."""
        from backend.src import schemas
        from backend.src.api.architect import redesign_phase

        project, phase, redesign_tasks, _ = await self._make_phase_with_tasks(
            db_session, redesign_count=2, done_count=1,
        )
        kept_task = redesign_tasks[0]
        deleted_task = redesign_tasks[1]

        llm_response = {
            "reasoning": "Task 2 is obsolete, adding new task instead",
            "tasks": [
                {
                    "id": str(kept_task.id),
                    "title": "Updated Task 1",
                    "description": "Modified description",
                    "worker_prompt": "New worker prompt",
                    "qa_prompt": "New qa prompt",
                    "priority": "medium",
                },
                {
                    "title": "Brand New Task",
                    "description": "From scratch",
                    "worker_prompt": "Build it",
                    "qa_prompt": "Test it",
                    "priority": "low",
                },
            ],
        }

        body = schemas.PhaseRedesignRequest()

        with (
            patch("backend.src.core.llm_client.create_llm_client_from_project") as mock_create,
            patch("backend.src.storage.redis_client.get_redis", new_callable=AsyncMock) as mock_get_redis,
        ):
            mock_client = AsyncMock()
            mock_client.structured_output = AsyncMock(return_value=llm_response)
            mock_create.return_value = mock_client

            mock_redis = AsyncMock()
            mock_get_redis.return_value = mock_redis

            result = await redesign_phase(phase_id=phase.id, body=body, db=db_session)

        assert result.phase_id == phase.id
        assert result.project_id == project.id
        assert result.reasoning == "Task 2 is obsolete, adding new task instead"
        assert result.tasks_kept == 1
        assert result.tasks_deleted == 1
        assert result.tasks_created == 1

    async def test_redesign_phase_not_found(self, db_session):
        """redesign_phase returns 404 for non-existent phase."""
        from backend.src import schemas
        from backend.src.api.architect import redesign_phase

        body = schemas.PhaseRedesignRequest()
        with pytest.raises(HTTPException) as exc_info:
            await redesign_phase(phase_id=uuid.uuid4(), body=body, db=db_session)
        assert exc_info.value.status_code == 404
        assert "Phase not found" in exc_info.value.detail

    async def test_redesign_phase_no_redesign_tasks(self, db_session):
        """redesign_phase returns 400 when phase has no tasks in redesign status."""
        from backend.src import schemas
        from backend.src.api.architect import redesign_phase
        from backend.src.models import Task, TaskStatus, TaskPriority

        project, phase = await create_project_with_phase(db_session)
        # Add a task in waiting status (not redesign)
        t = Task(
            id=uuid.uuid4(),
            project_id=project.id,
            phase_id=phase.id,
            title="Waiting Task",
            status=TaskStatus.waiting,
            priority=TaskPriority.medium,
        )
        db_session.add(t)
        await db_session.commit()

        body = schemas.PhaseRedesignRequest()
        with pytest.raises(HTTPException) as exc_info:
            await redesign_phase(phase_id=phase.id, body=body, db=db_session)
        assert exc_info.value.status_code == 400
        assert "No tasks in redesign status" in exc_info.value.detail

    async def test_redesign_phase_llm_error(self, db_session):
        """redesign_phase returns 502 on LLM failure."""
        from backend.src import schemas
        from backend.src.api.architect import redesign_phase
        from backend.src.core.llm_client import LLMError

        _, phase, _, _ = await self._make_phase_with_tasks(db_session)

        body = schemas.PhaseRedesignRequest()
        with patch("backend.src.core.llm_client.create_llm_client_from_project") as mock_create:
            mock_client = AsyncMock()
            mock_client.structured_output = AsyncMock(side_effect=LLMError("Rate limited"))
            mock_create.return_value = mock_client

            with pytest.raises(HTTPException) as exc_info:
                await redesign_phase(phase_id=phase.id, body=body, db=db_session)
            assert exc_info.value.status_code == 502
            assert "LLM error" in exc_info.value.detail

    async def test_redesign_phase_invalid_tasks_format(self, db_session):
        """redesign_phase returns 502 when LLM returns non-list tasks."""
        from backend.src import schemas
        from backend.src.api.architect import redesign_phase

        _, phase, _, _ = await self._make_phase_with_tasks(db_session)

        llm_response = {"reasoning": "ok", "tasks": "not a list"}
        body = schemas.PhaseRedesignRequest()
        with patch("backend.src.core.llm_client.create_llm_client_from_project") as mock_create:
            mock_client = AsyncMock()
            mock_client.structured_output = AsyncMock(return_value=llm_response)
            mock_create.return_value = mock_client

            with pytest.raises(HTTPException) as exc_info:
                await redesign_phase(phase_id=phase.id, body=body, db=db_session)
            assert exc_info.value.status_code == 502
            assert "invalid tasks format" in exc_info.value.detail

    async def test_redesign_phase_with_llm_config_override(self, db_session):
        """redesign_phase uses request body llm_config when provided."""
        from backend.src import schemas
        from backend.src.api.architect import redesign_phase

        _, phase, _, _ = await self._make_phase_with_tasks(db_session)

        llm_response = {"reasoning": "redesigned", "tasks": []}
        body = schemas.PhaseRedesignRequest(
            llm_config=schemas.LLMConfigInput(
                api_key="sk-override",
                model="gpt-4o",
                base_url="https://override.api",
            )
        )

        with (
            patch("backend.src.api.architect.LLMClient") as MockLLMClient,
            patch("backend.src.storage.redis_client.get_redis", new_callable=AsyncMock) as mock_get_redis,
        ):
            instance = MockLLMClient.return_value
            instance.structured_output = AsyncMock(return_value=llm_response)
            mock_get_redis.return_value = AsyncMock()

            result = await redesign_phase(phase_id=phase.id, body=body, db=db_session)

        assert result.tasks_deleted == 1  # the one redesign task was deleted
        assert result.tasks_created == 0
        # Verify LLMClient was called with override config
        MockLLMClient.assert_called_once()
        call_config = MockLLMClient.call_args[0][0]
        assert call_config.api_key == "sk-override"

    async def test_redesign_phase_project_not_found(self, db_session):
        """redesign_phase returns 404 if project is missing (orphaned phase)."""
        from backend.src import schemas
        from backend.src.api.architect import redesign_phase

        # Create phase without a valid project by patching project lookup
        _, phase, _, _ = await self._make_phase_with_tasks(db_session)

        body = schemas.PhaseRedesignRequest()
        with patch("backend.src.api.architect.ProjectRepository") as MockProjRepo:
            mock_repo = MockProjRepo.return_value
            mock_repo.get_by_id = AsyncMock(return_value=None)
            # Also need to keep PhaseRepository working
            with pytest.raises(HTTPException) as exc_info:
                await redesign_phase(phase_id=phase.id, body=body, db=db_session)
            assert exc_info.value.status_code == 404
            assert "Project not found" in exc_info.value.detail


# ── _clean_response unit tests ───────────────────────────────────────


class TestCleanResponse:
    """Direct unit tests for _clean_response helper."""

    def test_no_markers(self):
        from backend.src.api.architect import _clean_response

        cleaned, has_finalize, design_ctx = _clean_response("Hello, how can I help?")
        assert cleaned == "Hello, how can I help?"
        assert has_finalize is False
        assert design_ctx is None

    def test_finalize_only(self):
        from backend.src.api.architect import _clean_response

        cleaned, has_finalize, design_ctx = _clean_response("Done!\n[FINALIZE]")
        assert cleaned == "Done!"
        assert has_finalize is True
        assert design_ctx is None

    def test_design_context_and_finalize(self):
        from backend.src.api.architect import _clean_response

        text = "Here is the design.\n<design_context>\nProject: Test\nStack: Python\n</design_context>\n[FINALIZE]"
        cleaned, has_finalize, design_ctx = _clean_response(text)
        assert cleaned == "Here is the design."
        assert has_finalize is True
        assert design_ctx == "Project: Test\nStack: Python"

    def test_design_context_without_finalize(self):
        from backend.src.api.architect import _clean_response

        text = "Summary\n<design_context>\nSpec here\n</design_context>"
        cleaned, has_finalize, design_ctx = _clean_response(text)
        assert cleaned == "Summary"
        assert has_finalize is False
        assert design_ctx == "Spec here"
