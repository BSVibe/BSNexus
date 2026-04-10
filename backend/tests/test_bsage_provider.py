"""Tests for BSageMemoryProvider and the memory provider factory."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.src.core.memory import (
    BSageMemoryProvider,
    LocalMemoryProvider,
    make_memory_provider,
)


@pytest.fixture
def fake_http():
    http = MagicMock()
    http.post = AsyncMock()
    http.get = AsyncMock()
    http.delete = AsyncMock()
    return http


def _ok(json_payload):
    resp = MagicMock()
    resp.status_code = 200
    resp.json = MagicMock(return_value=json_payload)
    resp.raise_for_status = MagicMock()
    return resp


@pytest.mark.asyncio
async def test_remember_posts_to_bsage(fake_http):
    project_id = uuid.uuid4()
    record_id = uuid.uuid4()
    fake_http.post.return_value = _ok({
        "id": str(record_id),
        "project_id": str(project_id),
        "agent_id": None,
        "category": "decision",
        "title": "T",
        "content": "C",
        "metadata": {"k": "v"},
    })

    provider = BSageMemoryProvider("https://bsage.example.com/", "secret-key", http_client=fake_http)
    record = await provider.remember(
        project_id, None, category="decision", title="T", content="C", metadata={"k": "v"},
    )

    fake_http.post.assert_awaited_once()
    call = fake_http.post.await_args
    assert call.args[0] == "https://bsage.example.com/memories"
    assert call.kwargs["headers"]["Authorization"] == "Bearer secret-key"
    body = call.kwargs["json"]
    assert body["title"] == "T"
    assert body["metadata"] == {"k": "v"}
    assert record.id == record_id


@pytest.mark.asyncio
async def test_recall_filters_passed_as_query_params(fake_http):
    project_id = uuid.uuid4()
    fake_http.get.return_value = _ok({"items": []})

    provider = BSageMemoryProvider("https://bsage.example.com", "k", http_client=fake_http)
    out = await provider.recall(project_id, agent_id=None, category="learning", limit=5)
    assert out == []
    call = fake_http.get.await_args
    assert call.kwargs["params"]["category"] == "learning"
    assert call.kwargs["params"]["limit"] == "5"


@pytest.mark.asyncio
async def test_forget_returns_false_on_404(fake_http):
    resp = MagicMock()
    resp.status_code = 404
    resp.raise_for_status = MagicMock()
    fake_http.delete.return_value = resp

    provider = BSageMemoryProvider("https://bsage.example.com", "k", http_client=fake_http)
    deleted = await provider.forget(uuid.uuid4())
    assert deleted is False


@pytest.mark.asyncio
async def test_forget_returns_true_on_success(fake_http):
    fake_http.delete.return_value = _ok({})
    provider = BSageMemoryProvider("https://bsage.example.com", "k", http_client=fake_http)
    deleted = await provider.forget(uuid.uuid4())
    assert deleted is True


def test_factory_returns_local_provider_without_settings():
    provider = make_memory_provider(MagicMock(), tenant_settings=None)
    assert isinstance(provider, LocalMemoryProvider)


def test_factory_returns_local_provider_when_bsage_partial():
    """Both keys are required — partial config falls back to local."""
    provider = make_memory_provider(
        MagicMock(), tenant_settings={"bsage_base_url": "https://x"}
    )
    assert isinstance(provider, LocalMemoryProvider)


def test_factory_returns_bsage_provider_when_credentials_present():
    provider = make_memory_provider(
        MagicMock(),
        tenant_settings={"bsage_base_url": "https://bsage.example.com", "bsage_api_key": "k"},
    )
    assert isinstance(provider, BSageMemoryProvider)
