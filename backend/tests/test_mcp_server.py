"""HTTP-level test for the MCP server router.

The full MCP SSE protocol round-trip would need a running claude CLI
(it speaks JSON-RPC over SSE). We test the auth gate at HTTP boundary
here — the actual tool dispatch is covered by ``test_mcp_tools``,
which exercises the same async functions FastMCP's ``@app.tool()``
decorators delegate to.
"""

from __future__ import annotations

import uuid

import pytest

from backend.src.config import settings
from backend.src.mcp.auth import issue_run_scoped_token


def _good_token() -> str:
    return issue_run_scoped_token(
        {
            "run_id": str(uuid.uuid4()),
            "tenant_id": str(uuid.uuid4()),
            "project_id": str(uuid.uuid4()),
        },
        signing_key=settings.mcp_signing_key,
        ttl_seconds=300,
    )


@pytest.mark.asyncio
async def test_mcp_health_returns_claim_for_valid_token(client) -> None:
    token = _good_token()
    res = await client.get(f"/mcp/health?token={token}")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert "run_id" in body["claim"]
    assert "tenant_id" in body["claim"]
    assert "project_id" in body["claim"]
    # Liveness now also surfaces the registry's tool count for ops.
    assert isinstance(body["tool_count"], int)
    assert body["tool_count"] >= 26


@pytest.mark.asyncio
async def test_mcp_health_without_token_is_public_liveness(client) -> None:
    """TASK-005: ``GET /mcp/health`` without ``token=`` is the public
    liveness check that returns the registered tool count. Operators /
    load balancers use it to confirm the catalog booted."""
    res = await client.get("/mcp/health")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert "claim" not in body
    assert isinstance(body["tool_count"], int)
    assert body["tool_count"] >= 26


@pytest.mark.asyncio
async def test_mcp_health_rejects_bad_signature(client) -> None:
    bad = issue_run_scoped_token(
        {"run_id": "x", "tenant_id": "x", "project_id": "x"},
        signing_key="completely-different-key-32bytes-x",
        ttl_seconds=300,
    )
    res = await client.get(f"/mcp/health?token={bad}")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_mcp_health_rejects_expired_token(client) -> None:
    expired = issue_run_scoped_token(
        {"run_id": "x", "tenant_id": "x", "project_id": "x"},
        signing_key=settings.mcp_signing_key,
        ttl_seconds=-10,
    )
    res = await client.get(f"/mcp/health?token={expired}")
    assert res.status_code == 401


# ── ASGI gate: query-string parser must reject duplicate token keys ──


@pytest.mark.asyncio
async def test_sse_gate_rejects_duplicate_token_key() -> None:
    """``?token=a&token=b`` must 401 — silently picking ``a`` is a token-
    smuggling foothold (a careless reverse proxy could append ``token=``
    and the original parser would have ignored the second value)."""
    from backend.src.mcp.server import attach_to_app  # noqa: PLC0415

    captured: list[dict] = []

    class _CapturedSend:
        async def __call__(self, message: dict) -> None:
            captured.append(message)

    class _StubApp:
        def __init__(self) -> None:
            self._mounts: dict[str, object] = {}

        def mount(self, path: str, app: object) -> None:  # noqa: D401
            self._mounts[path] = app

    stub_app = _StubApp()
    attach_to_app(stub_app)
    gated = stub_app._mounts["/mcp/http"]  # type: ignore[index]

    good = _good_token()
    duped_qs = f"token={good}&token=other".encode()
    scope = {"type": "http", "query_string": duped_qs, "method": "GET", "path": "/"}

    async def _recv() -> dict:
        return {"type": "http.disconnect"}

    await gated(scope, _recv, _CapturedSend())  # type: ignore[arg-type]

    starts = [m for m in captured if m["type"] == "http.response.start"]
    assert starts and starts[0]["status"] == 401
