"""Phase 0 P0.7 — auth_provider swap end-to-end regression.

Decision #15 (Lockin §3): the swap from static api_key → service JWT
must be a closure replacement at the BaseServiceClient boundary —
adapter code (knowledge_client, audit_sink) does NOT change.

This file pins the contract end-to-end:

* The Sprint 2 PR #35 contract test (``test_auth_provider_swappable_for_service_jwt``)
  in ``tests/test_base_service_client.py`` already covers BaseServiceClient.
* The Sprint 4 PR #37 contract test (``test_auth_provider_swap_at_runtime_propagates_to_next_call``)
  in ``tests/test_sibling_clients_integration.py`` covers live runtime swap.
* This file covers **the actual P0.7 wire-in**: ``ServiceJWTMinter.make_auth_provider``
  feeding the existing adapters and producing service JWTs on the wire,
  with **zero changes** to ``BSageKnowledgeClient`` / ``BSupervisorAuditSink``.

Failing this test means the P0.7 swap is not actually a closure swap —
something in the adapter changed.
"""

from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

from backend.src.core.audit import BSupervisorAuditSink
from backend.src.core.composer import BSageKnowledgeClient
from backend.src.core.service_auth import ServiceJWTMinter


def _http_response(status: int = 200, payload: dict | None = None) -> MagicMock:
    r = MagicMock(spec=httpx.Response)
    r.status_code = status
    body = payload or {}
    r.json = MagicMock(return_value=body)
    r.content = b'{"x":1}' if payload is not None else b"{}"
    if status >= 400:
        r.raise_for_status = MagicMock(side_effect=httpx.HTTPStatusError("err", request=None, response=r))
    else:
        r.raise_for_status = MagicMock()
    return r


# ── Adapter source-line inspection (zero-change guard) ────────────────


def test_bsage_knowledge_client_adapter_is_constructible_with_minter_closure():
    """``BSageKnowledgeClient`` must be constructible with a service-JWT
    minter closure — not just a static api_key string. Pin: the adapter
    constructor surface DID NOT add a service_jwt parameter; the closure
    flows through ``BaseServiceClient.set_auth_provider``.
    """
    minter = ServiceJWTMinter(
        bsvibe_auth_url="https://auth.bsvibe.dev",
        client_id="bsnexus-test",
        client_secret="test-secret",
    )
    closure = minter.make_auth_provider(
        audience="bsage",
        tenant_id="11111111-1111-4111-8111-111111111111",
        scope=["bsage.read"],
    )

    # Construct with no static api_key, then swap closure on the underlying
    # BaseServiceClient. NO new BSageKnowledgeClient parameters were added.
    client = BSageKnowledgeClient("https://bsage.test", api_key=None)
    client._base.set_auth_provider(closure)

    # Sanity: the adapter still has the same public method surface.
    assert hasattr(client, "search")
    assert hasattr(client, "fetch")
    assert hasattr(client, "index")
    assert hasattr(client, "record_decision")


def test_bsupervisor_audit_sink_adapter_is_constructible_with_minter_closure():
    """Same contract for the audit sink — closure swap, no new params."""
    minter = ServiceJWTMinter(
        bsvibe_auth_url="https://auth.bsvibe.dev",
        client_id="bsnexus-test",
        client_secret="test-secret",
    )
    closure = minter.make_auth_provider(
        audience="bsupervisor",
        tenant_id="22222222-2222-4222-8222-222222222222",
        scope=["bsupervisor.write"],
    )

    sink = BSupervisorAuditSink("http://supervisor", api_key=None)
    sink._base.set_auth_provider(closure)

    # Public surface preserved.
    assert hasattr(sink, "preflight")
    assert hasattr(sink, "emit_post")


def test_bsage_knowledge_client_init_signature_did_not_drift():
    """Lock the BSageKnowledgeClient.__init__ parameters. P0.7 must NOT
    introduce a new positional parameter — that would be a breaking
    change for any caller that passes args positionally.

    If this test fails, the swap was NOT a closure replacement; the
    adapter was modified, violating Decision #15.
    """
    sig = inspect.signature(BSageKnowledgeClient.__init__)
    params = list(sig.parameters.keys())
    assert params == ["self", "base_url", "api_key", "auth_token", "timeout_s"], (
        "BSageKnowledgeClient.__init__ signature drifted; P0.7 swap is "
        "supposed to be a closure replacement, not a constructor change"
    )


def test_bsupervisor_audit_sink_init_signature_did_not_drift():
    """Same lock for BSupervisorAuditSink. New constructor params would
    indicate the swap leaked into adapter code."""
    sig = inspect.signature(BSupervisorAuditSink.__init__)
    params = list(sig.parameters.keys())
    assert params == ["self", "base_url", "api_key", "auth_token", "timeout_ms", "fail_mode"], (
        "BSupervisorAuditSink.__init__ signature drifted; P0.7 swap is "
        "supposed to be a closure replacement, not a constructor change"
    )


# ── End-to-end: minter → adapter → wire ─────────────────────────────


@pytest.mark.asyncio
async def test_bsage_search_uses_minted_service_jwt_when_swapped():
    """Wire test: build a minter, swap its closure into a BSageKnowledgeClient
    via the BaseServiceClient (zero adapter code change), and verify
    BSage sees a Bearer header carrying the service JWT — not a static
    api_key. Pin the cross-PR boundary."""
    minter = ServiceJWTMinter(
        bsvibe_auth_url="https://auth.bsvibe.dev",
        client_id="bsnexus-test",
        client_secret="test-secret",
    )

    issued = _http_response(200, {"access_token": "service-jwt-eyJ", "expires_in": 600})
    search_resp = _http_response(200, {"results": []})

    headers_seen: dict = {}

    async def mint_post(self, url, **kwargs):  # noqa: ANN001
        return issued

    async def search_request(self, method, url, **kwargs):  # noqa: ANN001
        if "/api/service-tokens/issue" in url:
            return issued
        headers_seen.update(kwargs.get("headers", {}))
        return search_resp

    client = BSageKnowledgeClient("https://bsage.test", api_key=None)
    client._base.set_auth_provider(
        minter.make_auth_provider(
            audience="bsage",
            tenant_id="t",
            scope=["bsage.read"],
        )
    )

    with (
        patch.object(httpx.AsyncClient, "post", new=mint_post),
        patch.object(httpx.AsyncClient, "request", new=search_request),
    ):
        await client.search("intent")

    assert headers_seen.get("Authorization") == "Bearer service-jwt-eyJ", (
        "BSage MUST see the minted service JWT in Authorization header — "
        "static api_key path is supposed to be retired in P0.7"
    )


@pytest.mark.asyncio
async def test_bsupervisor_post_uses_minted_service_jwt_when_swapped():
    """Same wire test for the audit sink — emit_post_async hits BSupervisor
    with the minted service JWT, not the static api_key."""
    minter = ServiceJWTMinter(
        bsvibe_auth_url="https://auth.bsvibe.dev",
        client_id="bsnexus-test",
        client_secret="test-secret",
    )

    issued = _http_response(200, {"access_token": "supervisor-svc-jwt", "expires_in": 600})
    post_resp = _http_response(200, {"ack": True})

    headers_seen: dict = {}

    async def mint_post(self, url, **kwargs):  # noqa: ANN001
        return issued

    async def supervisor_request(self, method, url, **kwargs):  # noqa: ANN001
        if "/api/service-tokens/issue" in url:
            return issued
        headers_seen.update(kwargs.get("headers", {}))
        return post_resp

    sink = BSupervisorAuditSink("http://supervisor", api_key=None)
    sink._base.set_auth_provider(
        minter.make_auth_provider(
            audience="bsupervisor",
            tenant_id="t",
            scope=["bsupervisor.write"],
        )
    )

    fake_run = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        status=SimpleNamespace(value="done"),
        actual_cost_cents=0,
    )

    with (
        patch.object(httpx.AsyncClient, "post", new=mint_post),
        patch.object(httpx.AsyncClient, "request", new=supervisor_request),
    ):
        await sink.emit_post(fake_run, {"status": "done"})

    assert headers_seen.get("Authorization") == "Bearer supervisor-svc-jwt"
