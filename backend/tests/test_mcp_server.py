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


@pytest.mark.asyncio
async def test_mcp_health_rejects_missing_token(client) -> None:
    res = await client.get("/mcp/health")
    assert res.status_code == 422  # FastAPI Query(...) required


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
