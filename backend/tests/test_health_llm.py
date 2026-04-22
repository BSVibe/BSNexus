"""Tests for /health/llm endpoint — validates base_url /v1 suffix handling."""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest


class _MockHttpxClient:
    """Mock httpx.AsyncClient that records requested URLs."""

    def __init__(self, requested_urls: list[str], fail_health: bool = False):
        self._urls = requested_urls
        self._fail_health = fail_health

    async def get(self, url, **kwargs):
        self._urls.append(str(url))
        if self._fail_health and "/health" in str(url):
            return httpx.Response(404)
        return httpx.Response(200, json={"data": []})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


async def _fake_get_db_with_url(url: str):
    """Fake get_db that returns a session-like object with the given URL in settings."""
    from unittest.mock import AsyncMock, MagicMock

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = url

    mock_db = AsyncMock()
    mock_db.execute.return_value = mock_result
    yield mock_db


async def _fake_get_db_empty():
    """Fake get_db that returns no settings."""
    from unittest.mock import AsyncMock, MagicMock

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None

    mock_db = AsyncMock()
    mock_db.execute.return_value = mock_result
    yield mock_db


@pytest.mark.asyncio
async def test_health_llm_strips_v1_suffix(client):
    """When llm_base_url ends with /v1, health check strips /v1 before building paths."""
    requested_urls: list[str] = []

    with (
        patch("backend.src.storage.database.get_db", lambda: _fake_get_db_with_url("http://ollama:11434/v1")),
        patch("httpx.AsyncClient", lambda **kw: _MockHttpxClient(requested_urls)),
    ):
        resp = await client.get("/health/llm")

    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"
    # server_root = http://ollama:11434 (stripped /v1)
    assert requested_urls[0] == "http://ollama:11434/health"


@pytest.mark.asyncio
async def test_health_llm_no_v1_suffix(client):
    """When llm_base_url has no /v1 suffix, paths are built without stripping."""
    requested_urls: list[str] = []

    with (
        patch("backend.src.storage.database.get_db", lambda: _fake_get_db_with_url("http://vllm:8888")),
        patch("httpx.AsyncClient", lambda **kw: _MockHttpxClient(requested_urls, fail_health=True)),
    ):
        resp = await client.get("/health/llm")

    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"
    assert requested_urls[0] == "http://vllm:8888/health"
    assert requested_urls[1] == "http://vllm:8888/v1/models"


@pytest.mark.asyncio
async def test_health_llm_v1_suffix_fallback(client):
    """When /health returns 404 and base_url has /v1, falls back to server_root/v1/models."""
    requested_urls: list[str] = []

    with (
        patch("backend.src.storage.database.get_db", lambda: _fake_get_db_with_url("http://ollama:11434/v1")),
        patch("httpx.AsyncClient", lambda **kw: _MockHttpxClient(requested_urls, fail_health=True)),
    ):
        resp = await client.get("/health/llm")

    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"
    # /v1 stripped → server_root = http://ollama:11434
    assert requested_urls[0] == "http://ollama:11434/health"
    assert requested_urls[1] == "http://ollama:11434/v1/models"


@pytest.mark.asyncio
async def test_health_llm_no_config(client):
    """When no llm_base_url is configured, returns unknown status."""
    with patch("backend.src.storage.database.get_db", _fake_get_db_empty):
        resp = await client.get("/health/llm")

    assert resp.status_code == 200
    assert resp.json()["status"] == "unknown"
