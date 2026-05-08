"""TASK-005 — HTTP /mcp surface + admin call route + registry catalog wiring.

Covers:
  * ``/mcp/health`` returns server liveness + tool count without any
    auth (ops smoke).
  * The shared :class:`ToolRegistry` returned by
    :func:`backend.src.mcp.server.get_registry` carries both domain and
    admin catalogs (>= 26 tools).
  * ``GET /mcp/admin/tools`` list endpoint with bearer (bootstrap) returns the
    catalog as JSON.
  * ``POST /mcp/admin/tools/{name}`` dispatches one admin tool and returns
    JSON. ``X-Tenant-Id`` lets the bootstrap caller pin a tenant the
    handler can scope by.
  * Both routes 401 when the bearer is missing or invalid.

Pattern matches the ``mcp-python-sdk-testing`` memory — no subprocess,
no real MCP protocol round-trip.
"""

from __future__ import annotations

import hashlib
import os
from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_mcp_health_returns_tool_count_without_token(client) -> None:
    """``/mcp/health`` is now public; returns liveness + tool count.

    Operators should be able to smoke-test the MCP catalog without
    minting a run-scoped token. The legacy token-claim verify path
    still works when ``token=...`` is supplied (covered by
    ``test_mcp_server.test_mcp_health_returns_claim_for_valid_token``).
    """
    res = await client.get("/mcp/health")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert isinstance(body["tool_count"], int)
    assert body["tool_count"] >= 26  # 6 domain + 20 admin


@pytest.mark.asyncio
async def test_get_registry_includes_admin_and_domain(monkeypatch) -> None:
    """``get_registry`` builds one catalog containing every tool —
    so HTTP and stdio see the same surface."""
    from backend.src.mcp import server as mcp_server  # noqa: PLC0415

    # Force a fresh build so this test isn't order-dependent.
    monkeypatch.setattr(mcp_server, "_registry", None)
    reg = mcp_server.get_registry()
    names = set(reg.names())
    # Domain tools.
    for n in (
        "decision_create",
        "decision_wait",
        "artifact_list",
        "artifact_read",
        "deliverable_report",
        "knowledge_search",
    ):
        assert n in names, f"domain tool {n!r} missing from registry"
    # A representative subset of admin tools (full list lives in
    # admin_tools.ADMIN_TOOL_NAMES).
    for n in (
        "bsnexus_projects_list",
        "bsnexus_requests_list",
        "bsnexus_decisions_list",
        "bsnexus_integrations_list",
    ):
        assert n in names, f"admin tool {n!r} missing from registry"


def _bootstrap_setup() -> tuple[str, str]:
    """Return ``(raw_token, sha256_digest)`` for a fresh bootstrap secret."""
    raw = "bsv_admin_lifespan-test-secret-001"
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return raw, digest


@pytest.mark.asyncio
async def test_mcp_list_tools_via_http_with_bootstrap(client) -> None:
    raw, digest = _bootstrap_setup()
    env = {k: v for k, v in os.environ.items() if k != "ENVIRONMENT"}
    with (
        patch.dict(os.environ, env, clear=True),
        patch("backend.src.core.auth.settings.bootstrap_token_hash", digest),
        patch("backend.src.core.auth.settings.e2e_test_token", ""),
    ):
        res = await client.get(
            "/mcp/admin/tools",
            headers={"Authorization": f"Bearer {raw}"},
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert "tools" in body
    names = {t["name"] for t in body["tools"]}
    assert "bsnexus_projects_list" in names
    assert "decision_create" in names
    # Each entry carries name + description + input_schema (snake_case
    # to match the rest of BSNexus' JSON envelopes).
    sample = next(t for t in body["tools"] if t["name"] == "bsnexus_projects_list")
    assert "description" in sample
    assert "input_schema" in sample
    assert "required_scopes" in sample


@pytest.mark.asyncio
async def test_mcp_list_tools_rejects_missing_bearer(client) -> None:
    res = await client.get("/mcp/admin/tools")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_mcp_list_tools_rejects_bad_bearer(client) -> None:
    _, digest = _bootstrap_setup()
    env = {k: v for k, v in os.environ.items() if k != "ENVIRONMENT"}
    with (
        patch.dict(os.environ, env, clear=True),
        patch("backend.src.core.auth.settings.bootstrap_token_hash", digest),
        patch("backend.src.core.auth.settings.e2e_test_token", ""),
    ):
        res = await client.get(
            "/mcp/admin/tools",
            headers={"Authorization": "Bearer bsv_admin_completely-wrong"},
        )
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_mcp_call_tool_with_bootstrap(client, mock_tenant_id, seeded_tenant) -> None:
    """End-to-end CallTool via HTTP: bootstrap header + ``X-Tenant-Id``
    header → admin tool runs against the test DB and returns JSON."""
    raw, digest = _bootstrap_setup()
    env = {k: v for k, v in os.environ.items() if k != "ENVIRONMENT"}
    with (
        patch.dict(os.environ, env, clear=True),
        patch("backend.src.core.auth.settings.bootstrap_token_hash", digest),
        patch("backend.src.core.auth.settings.e2e_test_token", ""),
    ):
        res = await client.post(
            "/mcp/admin/tools/bsnexus_projects_list",
            json={},
            headers={
                "Authorization": f"Bearer {raw}",
                "X-Tenant-Id": str(mock_tenant_id),
            },
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert "items" in body  # ProjectsListOutput shape
    assert isinstance(body["items"], list)


@pytest.mark.asyncio
async def test_mcp_call_tool_unknown_tool_404(client) -> None:
    raw, digest = _bootstrap_setup()
    env = {k: v for k, v in os.environ.items() if k != "ENVIRONMENT"}
    with (
        patch.dict(os.environ, env, clear=True),
        patch("backend.src.core.auth.settings.bootstrap_token_hash", digest),
        patch("backend.src.core.auth.settings.e2e_test_token", ""),
    ):
        res = await client.post(
            "/mcp/admin/tools/does_not_exist",
            json={},
            headers={"Authorization": f"Bearer {raw}"},
        )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_mcp_call_tool_validation_error_422(client, mock_tenant_id, seeded_tenant) -> None:
    raw, digest = _bootstrap_setup()
    env = {k: v for k, v in os.environ.items() if k != "ENVIRONMENT"}
    with (
        patch.dict(os.environ, env, clear=True),
        patch("backend.src.core.auth.settings.bootstrap_token_hash", digest),
        patch("backend.src.core.auth.settings.e2e_test_token", ""),
    ):
        # ``bsnexus_projects_create`` requires ``name``; missing → validation error.
        res = await client.post(
            "/mcp/admin/tools/bsnexus_projects_create",
            json={},
            headers={
                "Authorization": f"Bearer {raw}",
                "X-Tenant-Id": str(mock_tenant_id),
            },
        )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_mcp_call_tool_rejects_missing_bearer(client) -> None:
    res = await client.post("/mcp/admin/tools/bsnexus_projects_list", json={})
    assert res.status_code == 401
