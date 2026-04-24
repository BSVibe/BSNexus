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
    out = await NoopKnowledgeClient().index(payload={"reply_text": "y"})
    assert out is None


@pytest.mark.asyncio
async def test_noop_decision_returns_none():
    out = await NoopKnowledgeClient().record_decision(title="x", decision="y", reasoning="z")
    assert out is None


@pytest.mark.asyncio
async def test_bsage_index_posts_bsnexus_input_webhook():
    """index() forwards the raw run payload to BSage's bsnexus-input webhook.

    BSage's AgentLoop seed-refiner owns title derivation — sending a
    pre-computed title from BSNexus produces long, noisy note names like
    'Wrote 6 file(s): package.json, ...'. Forward the raw output and let
    BSage refine.
    """
    cm = _mock_httpx_post(
        json_payload={
            "plugin": "bsnexus-input",
            "results": [{"collected": 1}],
        }
    )
    with patch(
        "backend.src.core.composer.knowledge_client.httpx.AsyncClient",
        return_value=cm,
    ):
        client = BSageKnowledgeClient("https://bsage.test", "key")
        ref = await client.index(
            payload={
                "reply_text": "Shipped TODO app",
                "files": [{"path": "src/main.py", "size": 100}],
                "project": "todo",
                "run_id": "abc",
                "tags": ["project:todo", "bsnexus-deliverable"],
            },
        )

    # Webhook response doesn't include a note id/path (refiner runs after
    # plugin execute), so a successful post returns an anonymous ref.
    assert isinstance(ref, KnowledgeEntryRef)

    cm.post.assert_awaited_once()
    url, kwargs = cm.post.await_args.args, cm.post.await_args.kwargs
    assert url[0].endswith("/api/webhooks/bsnexus-input")
    body = kwargs["json"]
    # The body is forwarded verbatim — no title/content derivation.
    assert body["reply_text"] == "Shipped TODO app"
    assert body["files"] == [{"path": "src/main.py", "size": 100}]
    assert body["run_id"] == "abc"
    assert "bsnexus-deliverable" in body["tags"]


@pytest.mark.asyncio
async def test_bsage_index_fails_soft_on_500():
    cm = _mock_httpx_post(status=500, json_payload={"detail": "boom"})
    with patch(
        "backend.src.core.composer.knowledge_client.httpx.AsyncClient",
        return_value=cm,
    ):
        client = BSageKnowledgeClient("https://bsage.test", "key")
        ref = await client.index(payload={"reply_text": "x"})
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
    cfg = ProviderConfig(enabled=True, base_url="https://bsage.test", api_key="k")
    assert isinstance(resolve_knowledge_client(cfg), BSageKnowledgeClient)


def test_bsage_client_auth_token_overrides_api_key():
    client = BSageKnowledgeClient("https://bsage.test", "static-api-key", auth_token="user-jwt")
    assert client._headers["Authorization"] == "Bearer user-jwt"


def test_bsage_client_falls_back_to_api_key_without_token():
    client = BSageKnowledgeClient("https://bsage.test", "static-api-key")
    assert client._headers["Authorization"] == "Bearer static-api-key"


def test_bsage_client_no_auth_when_neither_set():
    client = BSageKnowledgeClient("https://bsage.test", None)
    assert "Authorization" not in client._headers


def test_headers_with_token_overrides_instance_default():
    client = BSageKnowledgeClient("https://bsage.test", "static")
    headers = client._headers_with_token("per-call")
    assert headers["Authorization"] == "Bearer per-call"
    assert client._headers["Authorization"] == "Bearer static"


def test_resolve_knowledge_client_threads_auth_token():
    cfg = ProviderConfig(enabled=True, base_url="https://bsage.test", api_key=None)
    client = resolve_knowledge_client(cfg, auth_token="jwt-xyz")
    assert isinstance(client, BSageKnowledgeClient)
    assert client._headers["Authorization"] == "Bearer jwt-xyz"


@pytest.mark.asyncio
async def test_bsage_record_decision_fails_soft_on_500():
    cm = _mock_httpx_post(status=500, json_payload={"detail": "boom"})
    with patch(
        "backend.src.core.composer.knowledge_client.httpx.AsyncClient",
        return_value=cm,
    ):
        client = BSageKnowledgeClient("https://bsage.test", "key")
        ref = await client.record_decision(title="x", decision="y", reasoning="z")
    assert ref is None


@pytest.mark.asyncio
async def test_bsage_search_returns_empty_on_500():
    from backend.src.core.composer import BSageKnowledgeClient as _C

    class _GetCM:
        def __init__(self):
            self.get = AsyncMock()
            resp = MagicMock()
            resp.raise_for_status = MagicMock(side_effect=RuntimeError("boom"))
            resp.json = MagicMock(return_value={})
            self.get.return_value = resp

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

    cm = _GetCM()
    with patch(
        "backend.src.core.composer.knowledge_client.httpx.AsyncClient",
        return_value=cm,
    ):
        client = _C("https://bsage.test", "key")
        fragments = await client.search("q")
    assert fragments == []


@pytest.mark.asyncio
async def test_bsage_index_uses_per_call_auth_token():
    cm = _mock_httpx_post(json_payload={"plugin": "bsnexus-input", "results": [{"collected": 1}]})
    with patch(
        "backend.src.core.composer.knowledge_client.httpx.AsyncClient",
        return_value=cm,
    ):
        client = BSageKnowledgeClient("https://bsage.test", "static")
        await client.index(payload={"reply_text": "x"}, auth_token="founder-jwt")

    sent_headers = cm.post.await_args.kwargs["headers"]
    assert sent_headers["Authorization"] == "Bearer founder-jwt"
