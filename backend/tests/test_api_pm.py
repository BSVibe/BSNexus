from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from backend.src.main import app


# -- Helpers -------------------------------------------------------------------


async def _async_iter(items: list):
    """Helper to create an async iterator from a list."""
    for item in items:
        yield item


# -- Fixtures ------------------------------------------------------------------


@pytest_asyncio.fixture
async def mock_redis() -> AsyncMock:
    """Create a mock Redis client."""
    r = AsyncMock()
    r.hset = AsyncMock()
    r.hgetall = AsyncMock(return_value={})
    r.hget = AsyncMock(return_value=None)
    r.expire = AsyncMock()
    r.exists = AsyncMock(return_value=1)
    r.set = AsyncMock()
    r.get = AsyncMock(return_value=None)
    r.delete = AsyncMock()
    r.scan_iter = MagicMock(return_value=_async_iter([]))
    return r


@pytest_asyncio.fixture
async def api_client(mock_redis: AsyncMock) -> AsyncGenerator[AsyncClient]:
    """Create an HTTP test client with mocked app state."""
    app.state.redis = mock_redis
    app.state.stream_manager = AsyncMock()
    app.state.orchestrators = {}

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client


# -- POST /api/v1/pm/{project_id}/start ------------------------------------------


async def test_start_orchestration(api_client: AsyncClient) -> None:
    """POST /api/v1/pm/start should start orchestration for a project."""
    project_id = str(uuid.uuid4())

    with patch("backend.src.api.pm.PMOrchestrator") as MockOrchestrator:
        mock_instance = AsyncMock()
        mock_instance.start = AsyncMock()
        MockOrchestrator.return_value = mock_instance

        response = await api_client.post(f"/api/v1/pm/{project_id}/start")

    assert response.status_code == 200
    data = response.json()
    assert data["detail"] == "Orchestration started"
    assert data["project_id"] == project_id


async def test_start_orchestration_already_running(api_client: AsyncClient) -> None:
    """POST /api/v1/pm/start should return 409 if orchestrator already running."""
    project_id = str(uuid.uuid4())

    # Pre-populate orchestrators dict
    app.state.orchestrators[project_id] = {
        "orchestrator": AsyncMock(),
        "task": AsyncMock(),
        "running": True,
    }

    response = await api_client.post(f"/api/v1/pm/{project_id}/start")

    assert response.status_code == 409
    assert "already running" in response.json()["detail"]

    # Cleanup
    del app.state.orchestrators[project_id]


# -- POST /api/v1/pm/{project_id}/pause ------------------------------------------


async def test_pause_orchestration(api_client: AsyncClient) -> None:
    """POST /api/v1/pm/pause should pause a running orchestrator."""
    project_id = str(uuid.uuid4())

    mock_orchestrator = AsyncMock()
    mock_orchestrator.stop = AsyncMock()

    app.state.orchestrators[project_id] = {
        "orchestrator": mock_orchestrator,
        "task": AsyncMock(),
        "running": True,
    }

    response = await api_client.post(f"/api/v1/pm/{project_id}/pause")

    assert response.status_code == 200
    data = response.json()
    assert data["detail"] == "Orchestration paused"
    assert data["project_id"] == project_id
    mock_orchestrator.stop.assert_called_once()

    # Cleanup
    del app.state.orchestrators[project_id]


async def test_pause_orchestration_not_running(api_client: AsyncClient) -> None:
    """POST /api/v1/pm/pause should return 404 if no orchestrator running."""
    project_id = str(uuid.uuid4())

    response = await api_client.post(f"/api/v1/pm/{project_id}/pause")

    assert response.status_code == 404
    assert "No running orchestrator" in response.json()["detail"]


# -- GET /api/v1/pm/{project_id}/status ------------------------------------------


async def test_get_status_not_running(api_client: AsyncClient, mock_redis: AsyncMock) -> None:
    """GET /api/v1/pm/status should return running=false when no orchestrator."""
    project_id = str(uuid.uuid4())

    # Mock the DB dependency
    mock_db = AsyncMock()

    async def override_get_db():
        yield mock_db

    from backend.src.storage.database import get_db as real_get_db

    app.dependency_overrides[real_get_db] = override_get_db

    with patch("backend.src.api.pm.TaskRepository") as MockRepo:
        mock_repo_instance = AsyncMock()
        mock_repo_instance.count_by_status = AsyncMock(return_value={})
        MockRepo.return_value = mock_repo_instance

        response = await api_client.get(f"/api/v1/pm/{project_id}/status")

    app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert data["project_id"] == project_id
    assert data["running"] is False
    assert "tasks" in data


async def test_get_status_running(api_client: AsyncClient, mock_redis: AsyncMock) -> None:
    """GET /api/v1/pm/status should return running=true when orchestrator is active."""
    project_id = str(uuid.uuid4())

    app.state.orchestrators[project_id] = {
        "orchestrator": AsyncMock(),
        "task": AsyncMock(),
        "running": True,
    }

    mock_db = AsyncMock()

    async def override_get_db():
        yield mock_db

    from backend.src.storage.database import get_db as real_get_db

    app.dependency_overrides[real_get_db] = override_get_db

    with patch("backend.src.api.pm.TaskRepository") as MockRepo:
        mock_repo_instance = AsyncMock()
        mock_repo_instance.count_by_status = AsyncMock(return_value={"ready": 3, "done": 5})
        MockRepo.return_value = mock_repo_instance

        response = await api_client.get(f"/api/v1/pm/{project_id}/status")

    app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert data["running"] is True

    # Cleanup
    del app.state.orchestrators[project_id]


# -- POST /api/v1/pm/{project_id}/queue-next -------------------------------------


async def test_queue_next_success(api_client: AsyncClient, mock_redis: AsyncMock) -> None:
    """POST /api/v1/pm/queue-next should queue the next ready task."""
    project_id = str(uuid.uuid4())

    with patch("backend.src.api.pm.PMOrchestrator") as MockOrchestrator:
        mock_task = MagicMock()
        mock_task.id = uuid.uuid4()
        mock_task.title = "Test Task"
        mock_task.priority.value = "high"

        mock_instance = AsyncMock()
        mock_instance.queue_next = AsyncMock(return_value=mock_task)
        MockOrchestrator.return_value = mock_instance

        from backend.src.storage.database import get_db as real_get_db

        mock_db = AsyncMock()

        async def override_get_db():
            yield mock_db

        app.dependency_overrides[real_get_db] = override_get_db

        response = await api_client.post(f"/api/v1/pm/{project_id}/queue-next")

        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert data["detail"] == "Task queued"
    assert data["title"] == "Test Task"
    assert data["priority"] == "high"


async def test_queue_next_no_ready_tasks(api_client: AsyncClient, mock_redis: AsyncMock) -> None:
    """POST /api/v1/pm/queue-next should return 404 when no ready tasks."""
    project_id = str(uuid.uuid4())

    with patch("backend.src.api.pm.PMOrchestrator") as MockOrchestrator:
        mock_instance = AsyncMock()
        mock_instance.queue_next = AsyncMock(return_value=None)
        MockOrchestrator.return_value = mock_instance

        from backend.src.storage.database import get_db as real_get_db

        mock_db = AsyncMock()

        async def override_get_db():
            yield mock_db

        app.dependency_overrides[real_get_db] = override_get_db

        response = await api_client.post(f"/api/v1/pm/{project_id}/queue-next")

        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert "No ready tasks" in response.json()["detail"]
