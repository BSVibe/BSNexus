"""BSage write paths — index() + record_decision() + Noop no-ops."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.src.core.composer import (
    BSageKnowledgeClient,
    KnowledgeEntryRef,
    NoopKnowledgeClient,
    resolve_knowledge_client,
)
from backend.src.core.integrations.config import ProviderConfig


def _mock_httpx_post(*, status: int = 201, json_payload: dict | None = None):
    """Build a context-manager mock that fakes httpx.AsyncClient.post()."""
    response = MagicMock()
    response.status_code = status
    response.json = MagicMock(return_value=json_payload or {})

    def _raise_for_status():
        if status >= 400:
            raise RuntimeError(f"http {status}")

    response.raise_for_status = _raise_for_status

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=cm)
    cm.__aexit__ = AsyncMock(return_value=None)
    cm.post = AsyncMock(return_value=response)
    return cm


@pytest.mark.asyncio
async def test_noop_index_returns_none():
    out = await NoopKnowledgeClient().index(title="x", content="y")
    assert out is None


@pytest.mark.asyncio
async def test_noop_decision_returns_none():
    out = await NoopKnowledgeClient().record_decision(
        title="x", decision="y", reasoning="z"
    )
    assert out is None


@pytest.mark.asyncio
async def test_bsage_index_posts_entries_endpoint():
    cm = _mock_httpx_post(
        json_payload={
            "id": "note-123",
            "path": "garden/idea/note-123.md",
            "created_at": "2026-04-24T00:00:00Z",
        }
    )
    with patch(
        "backend.src.core.composer.knowledge_client.httpx.AsyncClient",
        return_value=cm,
    ):
        client = BSageKnowledgeClient("https://bsage.test", "key")
        ref = await client.index(
            title="Shipped TODO app",
            content="Stack: Next.js + Prisma",
            tags=["project:todo", "bsnexus-deliverable"],
            source="bsnexus:todo",
            metadata={"bsnexus_run_id": "abc"},
        )

    assert isinstance(ref, KnowledgeEntryRef)
    assert ref.id == "note-123"
    assert ref.path == "garden/idea/note-123.md"

    cm.post.assert_awaited_once()
    url, kwargs = cm.post.await_args.args, cm.post.await_args.kwargs
    assert url[0].endswith("/api/knowledge/entries")
    body = kwargs["json"]
    assert body["title"] == "Shipped TODO app"
    assert "bsnexus-deliverable" in body["tags"]
    assert body["source"] == "bsnexus:todo"
    assert body["metadata"]["bsnexus_run_id"] == "abc"


@pytest.mark.asyncio
async def test_bsage_index_fails_soft_on_500():
    cm = _mock_httpx_post(status=500, json_payload={"detail": "boom"})
    with patch(
        "backend.src.core.composer.knowledge_client.httpx.AsyncClient",
        return_value=cm,
    ):
        client = BSageKnowledgeClient("https://bsage.test", "key")
        ref = await client.index(title="x", content="y")
    assert ref is None


@pytest.mark.asyncio
async def test_bsage_record_decision_posts_decisions_endpoint():
    cm = _mock_httpx_post(
        json_payload={
            "id": "dec-1",
            "path": "garden/insight/dec-1.md",
            "created_at": "2026-04-24T00:00:00Z",
        }
    )
    with patch(
        "backend.src.core.composer.knowledge_client.httpx.AsyncClient",
        return_value=cm,
    ):
        client = BSageKnowledgeClient("https://bsage.test", "key")
        ref = await client.record_decision(
            title="Pick Next.js over Remix",
            decision="Use Next.js 14 App Router",
            reasoning="Existing team familiarity + Prisma integration",
            alternatives=["Remix v2", "SvelteKit"],
            context="New TODO app project",
            tags=["project:todo"],
        )

    assert isinstance(ref, KnowledgeEntryRef)
    assert ref.id == "dec-1"
    url, kwargs = cm.post.await_args.args, cm.post.await_args.kwargs
    assert url[0].endswith("/api/knowledge/decisions")
    body = kwargs["json"]
    assert body["decision"] == "Use Next.js 14 App Router"
    assert body["alternatives"] == ["Remix v2", "SvelteKit"]


def test_resolve_knowledge_client_returns_noop_when_disabled():
    assert isinstance(resolve_knowledge_client(None), NoopKnowledgeClient)
    cfg = ProviderConfig(enabled=False, base_url="https://bsage.test", api_key=None)
    assert isinstance(resolve_knowledge_client(cfg), NoopKnowledgeClient)


def test_resolve_knowledge_client_returns_bsage_when_enabled():
    cfg = ProviderConfig(
        enabled=True, base_url="https://bsage.test", api_key="k"
    )
    assert isinstance(resolve_knowledge_client(cfg), BSageKnowledgeClient)
