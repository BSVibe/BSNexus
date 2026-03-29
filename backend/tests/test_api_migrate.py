"""Tests for the migrate API endpoint (SSE streaming, Claude Code CLI)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient

from backend.src.core.executor.base import ExecutionResult


@pytest_asyncio.fixture
async def sample_project_path(tmp_path: Path) -> str:
    """Create a sample project and return its path."""
    (tmp_path / "README.md").write_text("# Test Project\nA test project.")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "test-proj"')
    (tmp_path / ".git").mkdir()
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.py").write_text("print('hello')")
    return str(tmp_path)


_MOCK_CLI_JSON = {
    "project_name": "Test Project",
    "project_description": "A test project for unit testing.",
    "phases": [
        {
            "name": "Phase 1: Improvements",
            "description": "Immediate improvements",
            "tasks": [
                {
                    "title": "Add unit tests",
                    "description": "Add comprehensive test coverage",
                    "priority": "high",
                    "depends_on_indices": [],
                    "worker_prompt": "Write pytest tests for src/main.py",
                    "qa_prompt": "Check test coverage is above 80%",
                },
                {
                    "title": "Add type hints",
                    "description": "Add type annotations",
                    "priority": "medium",
                    "depends_on_indices": [0],
                    "worker_prompt": "Add type hints to all functions",
                    "qa_prompt": "Verify all public functions have type annotations",
                },
            ],
        },
        {
            "name": "Phase 2: Features",
            "description": "New features",
            "tasks": [
                {
                    "title": "Add logging",
                    "description": "Structured logging",
                    "priority": "medium",
                    "depends_on_indices": [],
                    "worker_prompt": "Add structlog logging",
                    "qa_prompt": "Check logging is structured JSON",
                },
            ],
        },
    ],
}

_MOCK_CLI_OUTPUT = json.dumps(_MOCK_CLI_JSON, ensure_ascii=False)


def _parse_sse_events(text: str) -> list[tuple[str, str]]:
    """Parse SSE text into list of (event, data) tuples."""
    events = []
    current_event = ""
    data_lines: list[str] = []
    for line in text.split("\n"):
        if line.startswith("event:"):
            current_event = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].strip())
        elif line.strip() == "":
            if current_event and data_lines:
                events.append((current_event, "\n".join(data_lines)))
            current_event = ""
            data_lines = []
    if current_event and data_lines:
        events.append((current_event, "\n".join(data_lines)))
    return events


def _mock_executor_success(output: str = _MOCK_CLI_OUTPUT) -> AsyncMock:
    """Create a mock executor that returns successful CLI result."""
    mock = AsyncMock()
    mock.execute = AsyncMock(return_value=ExecutionResult(success=True, stdout=output))
    return mock


def _mock_executor_failure(error: str = "CLI failed") -> AsyncMock:
    """Create a mock executor that returns failed CLI result."""
    mock = AsyncMock()
    mock.execute = AsyncMock(return_value=ExecutionResult(success=False, error_message=error, error_category="tool"))
    return mock


@pytest.mark.asyncio
class TestMigrateStreamEndpoint:
    async def test_migrate_creates_project(self, client: AsyncClient, sample_project_path: str, db_session) -> None:
        """Test successful migration via Claude Code CLI creates project."""
        with patch("backend.src.core.executor.claude_code.ClaudeCodeExecutor", return_value=_mock_executor_success()):
            response = await client.post(
                "/api/v1/architect/migrate/stream",
                json={"repo_path": sample_project_path},
            )

        assert response.status_code == 200
        events = _parse_sse_events(response.text)
        event_types = [e[0] for e in events]
        assert "step" in event_types
        assert "done" in event_types

        done_data = json.loads([e[1] for e in events if e[0] == "done"][0])
        assert "project_id" in done_data
        assert done_data["name"] == "Test Project"

    async def test_migrate_with_custom_name(self, client: AsyncClient, sample_project_path: str, db_session) -> None:
        with patch("backend.src.core.executor.claude_code.ClaudeCodeExecutor", return_value=_mock_executor_success()):
            response = await client.post(
                "/api/v1/architect/migrate/stream",
                json={"repo_path": sample_project_path, "name": "Custom Name"},
            )

        events = _parse_sse_events(response.text)
        done_data = json.loads([e[1] for e in events if e[0] == "done"][0])
        assert done_data["name"] == "Custom Name"

    async def test_migrate_creates_design_session(
        self, client: AsyncClient, sample_project_path: str, db_session
    ) -> None:
        from sqlalchemy import select
        from backend.src import models

        with patch("backend.src.core.executor.claude_code.ClaudeCodeExecutor", return_value=_mock_executor_success()):
            response = await client.post(
                "/api/v1/architect/migrate/stream",
                json={"repo_path": sample_project_path},
            )

        events = _parse_sse_events(response.text)
        done_data = json.loads([e[1] for e in events if e[0] == "done"][0])
        project_id = uuid.UUID(done_data["project_id"])

        result = await db_session.execute(
            select(models.DesignSession).where(models.DesignSession.project_id == project_id)
        )
        session = result.scalar_one_or_none()
        assert session is not None
        assert session.status == models.DesignSessionStatus.project_bound

    async def test_migrate_invalid_path(self, client: AsyncClient, db_session) -> None:
        response = await client.post(
            "/api/v1/architect/migrate/stream",
            json={"repo_path": "/nonexistent/path/to/project"},
        )

        events = _parse_sse_events(response.text)
        error_events = [e for e in events if e[0] == "error"]
        assert len(error_events) > 0
        assert "does not exist" in error_events[0][1]

    async def test_migrate_path_traversal_rejected(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/architect/migrate/stream",
            json={"repo_path": "../../../etc/passwd"},
        )
        assert response.status_code == 422

    async def test_migrate_cli_error(self, client: AsyncClient, sample_project_path: str, db_session) -> None:
        with patch(
            "backend.src.core.executor.claude_code.ClaudeCodeExecutor",
            return_value=_mock_executor_failure("claude: command not found"),
        ):
            response = await client.post(
                "/api/v1/architect/migrate/stream",
                json={"repo_path": sample_project_path},
            )

        events = _parse_sse_events(response.text)
        error_events = [e for e in events if e[0] == "error"]
        assert len(error_events) > 0
        assert "command not found" in error_events[0][1]

    async def test_migrate_task_dependencies(self, client: AsyncClient, sample_project_path: str, db_session) -> None:
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        from backend.src import models

        with patch("backend.src.core.executor.claude_code.ClaudeCodeExecutor", return_value=_mock_executor_success()):
            response = await client.post(
                "/api/v1/architect/migrate/stream",
                json={"repo_path": sample_project_path},
            )

        events = _parse_sse_events(response.text)
        done_data = json.loads([e[1] for e in events if e[0] == "done"][0])
        project_id = uuid.UUID(done_data["project_id"])

        result = await db_session.execute(
            select(models.Task)
            .where(models.Task.project_id == project_id)
            .options(selectinload(models.Task.depends_on))
            .order_by(models.Task.created_at)
        )
        tasks = list(result.scalars().all())
        assert len(tasks) == 3
        assert tasks[0].status == models.TaskStatus.ready
        assert tasks[1].status == models.TaskStatus.waiting
        assert len(tasks[1].depends_on) == 1
        assert tasks[2].status == models.TaskStatus.waiting

    async def test_migrate_progress_events(self, client: AsyncClient, sample_project_path: str, db_session) -> None:
        with patch("backend.src.core.executor.claude_code.ClaudeCodeExecutor", return_value=_mock_executor_success()):
            response = await client.post(
                "/api/v1/architect/migrate/stream",
                json={"repo_path": sample_project_path},
            )

        events = _parse_sse_events(response.text)
        step_events = [json.loads(e[1]) for e in events if e[0] == "step"]
        phases_seen = [s["phase"] for s in step_events]
        assert "analyze" in phases_seen
        assert "llm" in phases_seen
        assert "save" in phases_seen

    async def test_migrate_invalid_json_from_cli(
        self, client: AsyncClient, sample_project_path: str, db_session
    ) -> None:
        """Test that invalid JSON from CLI returns error event."""
        mock = AsyncMock()
        mock.execute = AsyncMock(return_value=ExecutionResult(success=True, stdout="This is not JSON at all"))
        with patch("backend.src.core.executor.claude_code.ClaudeCodeExecutor", return_value=mock):
            response = await client.post(
                "/api/v1/architect/migrate/stream",
                json={"repo_path": sample_project_path},
            )

        events = _parse_sse_events(response.text)
        error_events = [e for e in events if e[0] == "error"]
        assert len(error_events) > 0
        assert "JSON" in error_events[0][1]
