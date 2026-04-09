"""Integration test fixtures - reuses base test fixtures."""

import pytest
from unittest.mock import AsyncMock, patch


@pytest.fixture(autouse=True)
def mock_workspace_service(tmp_path):
    """Mock workspace service to use tmp_path instead of /data/workspaces."""
    mock_service = AsyncMock()
    mock_service.create_workspace = AsyncMock(return_value=str(tmp_path / "workspace"))
    mock_service.cleanup_workspace = AsyncMock()

    with (
        patch("backend.src.api.projects._workspace_service", mock_service),
        patch("backend.src.api.workspace._workspace_service", mock_service),
    ):
        yield mock_service
