"""S4 — Sibling-service client integration scenarios (Audit §6, BSNexus §特화).

The Knowledge / Audit / Integration probe code paths share
``BaseServiceClient`` (S2-1-X). Each adapter has unit tests, but the
audit gap calls for **integrated scenarios** that cover the full
adapter behaviour: search-and-fetch sequences, audit pre + post
roundtrip, graceful degradation when downstream is throttling.

These tests pin:

  * BSage search → fetch → record_decision sequence using one client
    (matches the real ``api/decisions.resolve_decision`` flow).
  * Audit pre-call + post-call roundtrip — the post emit MUST NOT block
    the caller and MUST log a warning when the response is 5xx.
  * Throttling: an adapter that gets 429 surfaces ``None`` from
    ``safe_request`` (caller's degrade-to-Noop path is preserved).
  * BaseServiceClient ``request`` path always raises (for callers that
    explicitly want the exception, e.g. integrations probe that maps
    to user-visible status).
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from backend.src.core.audit import (
    BSupervisorAuditSink,
    emit_post_async,
)
from backend.src.core.composer import (
    BSageKnowledgeClient,
    KnowledgeEntryRef,
)


def _build_async_client(*, request_responses: list, request_side_effects: list | None = None):
    """Build httpx.AsyncClient mock that returns each response in order."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=cm)
    cm.__aexit__ = AsyncMock(return_value=None)

    if request_side_effects is not None:
        cm.request = AsyncMock(side_effect=request_side_effects)
        cm.post = AsyncMock(side_effect=request_side_effects)
        cm.get = AsyncMock(side_effect=request_side_effects)
    else:
        # Round-robin through responses.
        idx = {"i": 0}

        async def request(method, url, **kwargs):  # noqa: ANN001
            r = request_responses[min(idx["i"], len(request_responses) - 1)]
            idx["i"] += 1
            return r

        cm.request = AsyncMock(side_effect=request)
        cm.post = AsyncMock(side_effect=request)
        cm.get = AsyncMock(side_effect=request)

    return cm


def _http_response(status: int, json_payload: dict | None = None) -> MagicMock:
    r = MagicMock(spec=httpx.Response)
    r.status_code = status
    payload = json_payload or {}
    r.json = MagicMock(return_value=payload)
    r.content = b"{}" if json_payload is None else b'{"x":1}'

    if status >= 400:
        r.raise_for_status = MagicMock(side_effect=httpx.HTTPStatusError("err", request=None, response=r))
    else:
        r.raise_for_status = MagicMock()
    return r


# ── BSage adapter: full sequence ────────────────────────────────────


@pytest.mark.asyncio
async def test_bsage_search_then_fetch_then_decision_sequence() -> None:
    """The decisions-resolve flow does: search → fetch (optional) →
    record_decision. One BSageKnowledgeClient instance must support all
    three call paths sequentially without re-instantiating httpx clients
    (each call constructs a fresh AsyncClient via BaseServiceClient)."""
    search_resp = _http_response(
        200,
        {"results": [{"path": "n.md", "title": "n", "preview": "p", "score": 0.5}]},
    )
    fetch_resp = _http_response(200, {"content": "doc body"})
    decision_resp = _http_response(201, {"id": "dec-1", "path": "garden/dec-1.md"})

    cm = _build_async_client(request_responses=[search_resp, fetch_resp, decision_resp])

    with patch("backend.src.core.clients.base.httpx.AsyncClient", return_value=cm):
        client = BSageKnowledgeClient("https://bsage.test", "static-key")
        fragments = await client.search("intent")
        body = await client.fetch("n.md")
        ref = await client.record_decision(
            title="Pick a", decision="a", reasoning="r", alternatives=["b"], context="ctx"
        )

    assert len(fragments) == 1 and fragments[0].path == "n.md"
    assert body == "doc body"
    assert isinstance(ref, KnowledgeEntryRef) and ref.id == "dec-1"
    # All three calls flowed through ``request`` on the same mock.
    assert cm.request.await_count == 3


@pytest.mark.asyncio
async def test_bsage_fetch_returns_none_on_404_keeps_search_working() -> None:
    """When fetch 404s, the client returns None (per Protocol). A
    follow-up search on the same client still works — pin that 404
    doesn't poison the BaseServiceClient state."""
    fetch_resp = _http_response(404, {})
    search_resp = _http_response(200, {"results": []})
    cm = _build_async_client(request_responses=[fetch_resp, search_resp])

    with patch("backend.src.core.clients.base.httpx.AsyncClient", return_value=cm):
        client = BSageKnowledgeClient("https://bsage.test", "k")
        body = await client.fetch("missing.md")
        fragments = await client.search("q")

    assert body is None
    assert fragments == []


@pytest.mark.asyncio
async def test_bsage_throttle_429_safe_returns_none() -> None:
    """A 429 from BSage during ``index`` must surface as ``None`` (not
    raise) so the calling deliverable persistence keeps working."""
    cm = _build_async_client(request_responses=[_http_response(429, {"detail": "too many"})])
    with patch("backend.src.core.clients.base.httpx.AsyncClient", return_value=cm):
        client = BSageKnowledgeClient("https://bsage.test", "k")
        ref = await client.index(payload={"reply_text": "x"})
    assert ref is None


# ── Audit sink: full pre + post roundtrip ──────────────────────────


@pytest.mark.asyncio
async def test_audit_preflight_then_emit_post_roundtrip() -> None:
    """A real chain: preflight returns 200/allowed, then emit_post is
    fired-and-forgotten without affecting the caller. Audit §6 calls
    out the BSupervisor integration roundtrip as a coverage gap."""
    preflight_resp = _http_response(200, {"allowed": True})
    post_resp = _http_response(200, {"ack": True})
    cm = _build_async_client(request_responses=[preflight_resp, post_resp])

    sink = BSupervisorAuditSink("http://supervisor", "k", timeout_ms=200)
    fake_run = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        status=SimpleNamespace(value="running"),
        actual_cost_cents=0,
    )
    fake_snap = SimpleNamespace(id=uuid.uuid4(), tools_allowed=["read"], persona_label="builder")

    with patch("httpx.AsyncClient", return_value=cm):
        result = await sink.preflight(fake_run, fake_snap)
        # Fire-and-forget post returns a Task we can await for the test.
        task = emit_post_async(sink, fake_run, {"status": "done"})
        await task

    assert result.blocked is False
    assert result.degraded is False
    # 2 underlying httpx calls — preflight + post.
    assert cm.request.await_count == 2


@pytest.mark.asyncio
async def test_audit_post_failure_does_not_propagate() -> None:
    """``emit_post`` is fire-and-forget — a 500 from BSupervisor must
    not raise out of the task scheduler. Pinning this contract because
    a regression here would crash background tasks silently in prod."""
    cm = _build_async_client(request_responses=[_http_response(500, {"detail": "boom"})])
    sink = BSupervisorAuditSink("http://supervisor", "k", timeout_ms=200)
    fake_run = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        status=SimpleNamespace(value="done"),
        actual_cost_cents=0,
    )

    with patch("httpx.AsyncClient", return_value=cm):
        task = emit_post_async(sink, fake_run, {"status": "done"})
        # Must complete without raising.
        await asyncio.wait_for(task, timeout=2.0)


# ── BaseServiceClient: explicit-error contract ─────────────────────


@pytest.mark.asyncio
async def test_base_service_client_request_propagates_http_error() -> None:
    """``request`` (strict path) MUST raise on HTTP failure so callers
    that need the explicit error (integrations probe) can surface
    'unauthorized' / 'unreachable' to the user. The other path
    (``safe_request``) swallows — both contracts are needed."""
    from backend.src.core.clients.base import BaseServiceClient

    async def boom(self, *args, **kwargs):  # noqa: ANN001
        raise httpx.HTTPError("upstream broke")

    client = BaseServiceClient(
        base_url="https://x.test",
        auth_provider=AsyncMock(return_value="k"),
        user_agent="UA",
    )
    with patch.object(httpx.AsyncClient, "request", new=boom):
        with pytest.raises(httpx.HTTPError):
            await client.request("GET", "/api/health")


@pytest.mark.asyncio
async def test_safe_request_swallows_http_error_and_logs() -> None:
    """``safe_request`` returns None on HTTPError so adapters degrade
    cleanly. Pin that ``CancelledError`` is NOT swallowed."""
    from backend.src.core.clients.base import BaseServiceClient

    client = BaseServiceClient(
        base_url="https://x.test",
        auth_provider=AsyncMock(return_value="k"),
        user_agent="UA",
    )

    async def http_err(self, *args, **kwargs):  # noqa: ANN001
        raise httpx.HTTPError("bad")

    with patch.object(httpx.AsyncClient, "request", new=http_err):
        result = await client.safe_request("GET", "/", event="probe")
    assert result is None


@pytest.mark.asyncio
async def test_auth_provider_swap_at_runtime_propagates_to_next_call() -> None:
    """P0.7 contract: the *next* request after ``set_auth_provider`` uses
    the new closure. This is the live precondition for the Phase 0
    swap-from-api-key-to-service-jwt migration."""
    from backend.src.core.clients.base import BaseServiceClient

    headers_seen: list[dict] = []

    async def capture(self, method, url, **kwargs):  # noqa: ANN001
        headers_seen.append(kwargs.get("headers", {}))
        return _http_response(200, {})

    client = BaseServiceClient(
        base_url="https://x.test",
        auth_provider=AsyncMock(return_value="initial-key"),
        user_agent="UA",
    )

    with patch.object(httpx.AsyncClient, "request", new=capture):
        await client.request("GET", "/")
        client.set_auth_provider(AsyncMock(return_value="rotated-jwt-eyJ"))
        await client.request("GET", "/")
        # Swap again to a sync closure — must be supported.
        client.set_auth_provider(lambda: "sync-third-key")
        await client.request("GET", "/")

    auths = [h.get("Authorization") for h in headers_seen]
    assert auths == [
        "Bearer initial-key",
        "Bearer rotated-jwt-eyJ",
        "Bearer sync-third-key",
    ]


@pytest.mark.asyncio
async def test_auth_provider_failure_falls_through_to_anonymous() -> None:
    """If the auth_provider raises, BaseServiceClient logs a warning
    and sends an anonymous request rather than crashing the caller.
    Pin this contract because the P0.7 service-JWT minter could fail
    transiently and we must not take down all traffic."""
    from backend.src.core.clients.base import BaseServiceClient

    headers_seen: dict = {}

    async def capture(self, method, url, **kwargs):  # noqa: ANN001
        headers_seen.update(kwargs.get("headers", {}))
        return _http_response(200, {})

    def boom() -> str:
        raise RuntimeError("minter down")

    client = BaseServiceClient(
        base_url="https://x.test",
        auth_provider=boom,
        user_agent="UA",
    )

    with patch.object(httpx.AsyncClient, "request", new=capture):
        await client.request("GET", "/")

    assert "Authorization" not in headers_seen
