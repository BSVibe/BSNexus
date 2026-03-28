"""Tests for KnowledgeProvider protocol and implementations.

TDD: Written BEFORE implementation code.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

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
# BSageProvider tests
# ---------------------------------------------------------------------------
class TestBSageProvider:
    @pytest.fixture
    def provider(self) -> BSageProvider:
        return BSageProvider(
            base_url="https://bsage.example.com",
            api_key="test-bsage-key-1234",
            timeout=30.0,
        )

    async def test_get_sot_success(self, provider: BSageProvider) -> None:
        """Should GET source-of-truth from BSage API."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "project_id": "p-123",
            "architecture": "monolith",
            "tech_stack": ["python", "fastapi"],
        }
        mock_response.raise_for_status = MagicMock()

        with patch("backend.src.providers.knowledge.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await provider.get_sot("p-123")

        assert result["project_id"] == "p-123"
        assert result["architecture"] == "monolith"

        mock_client.get.assert_called_once()
        call_args = mock_client.get.call_args
        assert call_args[0][0] == "https://bsage.example.com/api/knowledge/sot/p-123"

    async def test_get_sot_sends_auth_header(self, provider: BSageProvider) -> None:
        """API key should be sent as Bearer token."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_response.raise_for_status = MagicMock()

        with patch("backend.src.providers.knowledge.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            await provider.get_sot("p-123")

        call_args = mock_client.get.call_args
        headers = call_args[1].get("headers", {})
        assert headers["Authorization"] == "Bearer test-bsage-key-1234"

    async def test_get_sot_http_error(self, provider: BSageProvider) -> None:
        """Should raise on HTTP errors."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Not Found", request=MagicMock(), response=mock_response
        )

        with patch("backend.src.providers.knowledge.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            with pytest.raises(httpx.HTTPStatusError):
                await provider.get_sot("p-unknown")

    async def test_store_result_success(self, provider: BSageProvider) -> None:
        """Should POST result to BSage API."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.raise_for_status = MagicMock()

        result_data = {"status": "done", "output": "code generated"}

        with patch("backend.src.providers.knowledge.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            await provider.store_result("t-456", result_data)

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "https://bsage.example.com/api/knowledge/results/t-456"
        body = call_args[1].get("json", {})
        assert body["task_id"] == "t-456"
        assert body["result"] == result_data

    async def test_store_result_http_error(self, provider: BSageProvider) -> None:
        """Should raise on HTTP errors."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server Error", request=MagicMock(), response=mock_response
        )

        with patch("backend.src.providers.knowledge.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            with pytest.raises(httpx.HTTPStatusError):
                await provider.store_result("t-456", {"status": "done"})

    async def test_search_success(self, provider: BSageProvider) -> None:
        """Should POST search query to BSage API and return results."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {"id": "doc-1", "content": "FastAPI setup", "score": 0.95},
                {"id": "doc-2", "content": "Database config", "score": 0.87},
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("backend.src.providers.knowledge.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            results = await provider.search("FastAPI patterns", limit=5)

        assert len(results) == 2
        assert results[0]["id"] == "doc-1"

        call_args = mock_client.post.call_args
        assert call_args[0][0] == "https://bsage.example.com/api/knowledge/search"
        body = call_args[1].get("json", {})
        assert body["query"] == "FastAPI patterns"
        assert body["limit"] == 5

    async def test_search_empty_results(self, provider: BSageProvider) -> None:
        """Should return empty list when no results found."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": []}
        mock_response.raise_for_status = MagicMock()

        with patch("backend.src.providers.knowledge.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

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
