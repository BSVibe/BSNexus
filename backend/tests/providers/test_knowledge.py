"""Tests for KnowledgeProvider protocol and implementations.

TDD: Written BEFORE implementation code.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from backend.src.providers.knowledge import (
    BSageProvider,
    KnowledgeProvider,
    LocalMarkdownProvider,
)


# ---------------------------------------------------------------------------
# Protocol compliance tests
# ---------------------------------------------------------------------------
class TestKnowledgeProviderProtocol:
    """Verify KnowledgeProvider is a typing.Protocol with correct methods."""

    def test_protocol_is_runtime_checkable(self) -> None:
        assert hasattr(KnowledgeProvider, "__protocol_attrs__") or hasattr(
            KnowledgeProvider, "__abstractmethods__"
        ), "KnowledgeProvider must be a Protocol"

    def test_compliant_class_is_instance(self) -> None:
        class _FakeKnowledge:
            async def get_sot(self, project_id: str) -> dict[str, Any]:
                return {}

            async def store_result(self, task_id: str, result: dict[str, Any]) -> None:
                pass

            async def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
                return []

        assert isinstance(_FakeKnowledge(), KnowledgeProvider)

    def test_non_compliant_class_is_not_instance(self) -> None:
        class _Incomplete:
            pass

        assert not isinstance(_Incomplete(), KnowledgeProvider)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _mock_response(
    status_code: int = 200,
    json_data: dict[str, Any] | None = None,
    raise_error: httpx.HTTPStatusError | None = None,
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    if raise_error:
        resp.raise_for_status.side_effect = raise_error
    else:
        resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# BSageProvider tests
# ---------------------------------------------------------------------------
class TestBSageProvider:
    @pytest.fixture
    def mock_client(self) -> AsyncMock:
        return AsyncMock()

    @pytest.fixture
    def provider(self, mock_client: AsyncMock) -> BSageProvider:
        return BSageProvider(
            base_url="https://bsage.example.com",
            api_key="test-bsage-key-1234",
            timeout=30.0,
            client=mock_client,
        )

    async def test_get_sot_success(self, provider: BSageProvider, mock_client: AsyncMock) -> None:
        """Should GET source-of-truth from BSage API."""
        mock_client.get = AsyncMock(return_value=_mock_response(
            json_data={
                "project_id": "p-123",
                "architecture": "monolith",
                "tech_stack": ["python", "fastapi"],
            }
        ))

        result = await provider.get_sot("p-123")

        assert result["project_id"] == "p-123"
        assert result["architecture"] == "monolith"

        mock_client.get.assert_called_once()
        call_args = mock_client.get.call_args
        assert call_args[0][0] == "https://bsage.example.com/api/knowledge/sot/p-123"

    async def test_get_sot_sends_auth_header(self, provider: BSageProvider, mock_client: AsyncMock) -> None:
        """API key should be sent as Bearer token."""
        mock_client.get = AsyncMock(return_value=_mock_response(json_data={}))

        await provider.get_sot("p-123")

        call_args = mock_client.get.call_args
        headers = call_args[1].get("headers", {})
        assert headers["Authorization"] == "Bearer test-bsage-key-1234"

    async def test_get_sot_http_error(self, provider: BSageProvider, mock_client: AsyncMock) -> None:
        """Should raise on HTTP errors."""
        error_resp = MagicMock()
        error_resp.status_code = 404
        mock_client.get = AsyncMock(return_value=_mock_response(
            status_code=404,
            raise_error=httpx.HTTPStatusError("Not Found", request=MagicMock(), response=error_resp),
        ))

        with pytest.raises(httpx.HTTPStatusError):
            await provider.get_sot("p-unknown")

    async def test_store_result_success(self, provider: BSageProvider, mock_client: AsyncMock) -> None:
        """Should POST result to BSage API."""
        mock_client.post = AsyncMock(return_value=_mock_response(status_code=201))

        result_data = {"status": "done", "output": "code generated"}
        await provider.store_result("t-456", result_data)

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "https://bsage.example.com/api/knowledge/results/t-456"
        body = call_args[1].get("json", {})
        assert body["task_id"] == "t-456"
        assert body["result"] == result_data

    async def test_store_result_http_error(self, provider: BSageProvider, mock_client: AsyncMock) -> None:
        """Should raise on HTTP errors."""
        error_resp = MagicMock()
        error_resp.status_code = 500
        mock_client.post = AsyncMock(return_value=_mock_response(
            status_code=500,
            raise_error=httpx.HTTPStatusError("Server Error", request=MagicMock(), response=error_resp),
        ))

        with pytest.raises(httpx.HTTPStatusError):
            await provider.store_result("t-456", {"status": "done"})

    async def test_search_success(self, provider: BSageProvider, mock_client: AsyncMock) -> None:
        """Should POST search query to BSage API and return results."""
        mock_client.post = AsyncMock(return_value=_mock_response(
            json_data={
                "results": [
                    {"id": "doc-1", "content": "FastAPI setup", "score": 0.95},
                    {"id": "doc-2", "content": "Database config", "score": 0.87},
                ]
            }
        ))

        results = await provider.search("FastAPI patterns", limit=5)

        assert len(results) == 2
        assert results[0]["id"] == "doc-1"

        call_args = mock_client.post.call_args
        assert call_args[0][0] == "https://bsage.example.com/api/knowledge/search"
        body = call_args[1].get("json", {})
        assert body["query"] == "FastAPI patterns"
        assert body["limit"] == 5

    async def test_search_empty_results(self, provider: BSageProvider, mock_client: AsyncMock) -> None:
        """Should return empty list when no results found."""
        mock_client.post = AsyncMock(return_value=_mock_response(json_data={"results": []}))

        results = await provider.search("nonexistent", limit=10)

        assert results == []


# ---------------------------------------------------------------------------
# LocalMarkdownProvider tests
# ---------------------------------------------------------------------------
class TestLocalMarkdownProvider:
    @pytest.fixture
    def provider(self, tmp_path) -> LocalMarkdownProvider:
        return LocalMarkdownProvider(knowledge_dir=tmp_path)

    async def test_get_sot_reads_project_file(self, provider: LocalMarkdownProvider, tmp_path) -> None:
        """Should read source-of-truth from a markdown file for the project."""
        sot_file = tmp_path / "sot" / "p-123.md"
        sot_file.parent.mkdir(parents=True, exist_ok=True)
        sot_file.write_text("# Project p-123\n\nArchitecture: monolith\nStack: Python, FastAPI\n")

        result = await provider.get_sot("p-123")

        assert result["project_id"] == "p-123"
        assert "# Project p-123" in result["content"]

    async def test_get_sot_returns_empty_when_not_found(self, provider: LocalMarkdownProvider) -> None:
        """Should return empty dict when project SOT file doesn't exist."""
        result = await provider.get_sot("p-nonexistent")

        assert result == {}

    async def test_store_result_writes_file(self, provider: LocalMarkdownProvider, tmp_path) -> None:
        """Should write result to a markdown file."""
        result_data = {"status": "done", "output": "generated code"}

        await provider.store_result("t-789", result_data)

        result_file = tmp_path / "results" / "t-789.md"
        assert result_file.exists()
        content = result_file.read_text()
        assert "t-789" in content
        assert "done" in content

    async def test_store_result_creates_directory(self, provider: LocalMarkdownProvider, tmp_path) -> None:
        """Should create results directory if it doesn't exist."""
        await provider.store_result("t-new", {"status": "complete"})

        result_file = tmp_path / "results" / "t-new.md"
        assert result_file.exists()

    async def test_search_matches_content(self, provider: LocalMarkdownProvider, tmp_path) -> None:
        """Should search markdown files for matching content."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        (docs_dir / "api-design.md").write_text("# API Design\n\nRESTful endpoints with FastAPI\n")
        (docs_dir / "database.md").write_text("# Database\n\nPostgreSQL with SQLAlchemy\n")
        (docs_dir / "testing.md").write_text("# Testing\n\npytest with async support\n")

        results = await provider.search("FastAPI", limit=10)

        assert len(results) >= 1
        matched_files = [r["file"] for r in results]
        assert any("api-design" in f for f in matched_files)

    async def test_search_respects_limit(self, provider: LocalMarkdownProvider, tmp_path) -> None:
        """Should return at most `limit` results."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        for i in range(5):
            (docs_dir / f"doc-{i}.md").write_text(f"# Doc {i}\n\nCommon keyword here\n")

        results = await provider.search("keyword", limit=2)

        assert len(results) <= 2

    async def test_search_no_matches(self, provider: LocalMarkdownProvider, tmp_path) -> None:
        """Should return empty list when nothing matches."""
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        (docs_dir / "test.md").write_text("# Test\n\nNothing relevant here\n")

        results = await provider.search("zzzznonexistent", limit=10)

        assert results == []

    async def test_search_empty_directory(self, provider: LocalMarkdownProvider) -> None:
        """Should return empty list when docs directory doesn't exist."""
        results = await provider.search("anything", limit=10)

        assert results == []
