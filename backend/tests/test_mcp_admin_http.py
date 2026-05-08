"""HTTP transport for admin MCP tools (TASK-005).

The admin transport differs from the run-scoped FastMCP transport at
``/mcp/http``: it authenticates with ``Authorization: Bearer <token>``
through :func:`backend.src.mcp.api.resolve_tool_context` (bootstrap /
opaque / JWT) rather than the per-run HMAC token. The wire shape is a
small REST envelope on top of the same shared :class:`ToolRegistry` —
``GET /mcp/admin/tools`` returns the catalog and
``POST /mcp/admin/tools/{name}`` dispatches.

These tests verify the lifespan-mounted endpoints work end-to-end with a
bootstrap admin token and that the public ``/mcp/health`` endpoint
returns the registry's tool count without requiring auth.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from bsvibe_authz import User


# ── /mcp/health ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mcp_health_no_token_returns_liveness_and_tool_count(client) -> None:
    """``GET /mcp/health`` without ``token=`` is a public liveness check
    that includes the registered tool count. Operators / load balancers
    use it to confirm the MCP catalog booted before forwarding traffic.
    """
    res = await client.get("/mcp/health")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert isinstance(body["tool_count"], int)
    # Domain (6) + admin (20) tools register on the shared registry.
    assert body["tool_count"] >= 26


# ── /mcp/admin/tools — list ────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_list_tools_requires_bearer(client) -> None:
    res = await client.get("/mcp/admin/tools")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_admin_list_tools_with_bootstrap_returns_catalog(client, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_user = User(id="admin@bsvibe.dev", is_service=True, scope=["*"])

    def fake_verify_bootstrap(token: str, az: Any) -> User:
        assert token == "bsv_admin_test"
        return fake_user

    monkeypatch.setattr("backend.src.mcp.api.verify_bootstrap_token", fake_verify_bootstrap)
    monkeypatch.setattr("backend.src.mcp.api._authz_settings", lambda: MagicMock())

    res = await client.get(
        "/mcp/admin/tools",
        headers={"Authorization": "Bearer bsv_admin_test"},
    )
    assert res.status_code == 200
    body = res.json()
    names = {t["name"] for t in body["tools"]}
    # spot-check: catalog includes both domain and admin tools
    assert "decision_create" in names
    assert "bsnexus_projects_list" in names


# ── /mcp/admin/tools/{name} — call ─────────────────────────────────


@pytest.mark.asyncio
async def test_admin_call_tool_requires_bearer(client) -> None:
    res = await client.post("/mcp/admin/tools/bsnexus_projects_list", json={"limit": 50})
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_admin_call_tool_dispatches_via_registry(client, mock_tenant_id, monkeypatch: pytest.MonkeyPatch) -> None:
    """A bootstrap-authenticated POST goes through the shared
    :class:`ToolRegistry`. We verify the dispatch path by stubbing
    ``ToolRegistry.call_tool`` and asserting the HTTP response carries
    the handler's structured output verbatim.
    """
    from pydantic import BaseModel

    class _StubOut(BaseModel):
        ok: bool
        echoed: str

    fake_user = User(
        id="admin@bsvibe.dev",
        is_service=True,
        scope=["*"],
        active_tenant_id=str(mock_tenant_id),
    )

    def fake_verify_bootstrap(token: str, az: Any) -> User:
        return fake_user

    captured: dict[str, Any] = {}

    async def fake_call_tool(self, name, args, ctx):  # noqa: ANN001
        captured["name"] = name
        captured["args"] = dict(args)
        captured["user"] = ctx.user
        return _StubOut(ok=True, echoed=name)

    monkeypatch.setattr("backend.src.mcp.api.verify_bootstrap_token", fake_verify_bootstrap)
    monkeypatch.setattr("backend.src.mcp.api._authz_settings", lambda: MagicMock())
    monkeypatch.setattr("backend.src.mcp.api.ToolRegistry.call_tool", fake_call_tool)

    res = await client.post(
        "/mcp/admin/tools/bsnexus_projects_list",
        json={"limit": 25},
        headers={"Authorization": "Bearer bsv_admin_test"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body == {"ok": True, "echoed": "bsnexus_projects_list"}
    assert captured["name"] == "bsnexus_projects_list"
    assert captured["args"] == {"limit": 25}
    assert captured["user"] is fake_user


@pytest.mark.asyncio
async def test_admin_call_tool_unknown_returns_404(client, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_user = User(id="admin@bsvibe.dev", is_service=True, scope=["*"])

    monkeypatch.setattr(
        "backend.src.mcp.api.verify_bootstrap_token",
        lambda *_a, **_kw: fake_user,
    )
    monkeypatch.setattr("backend.src.mcp.api._authz_settings", lambda: MagicMock())

    res = await client.post(
        "/mcp/admin/tools/does_not_exist",
        json={},
        headers={"Authorization": "Bearer bsv_admin_test"},
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_admin_call_tool_validation_error_returns_422(client, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pydantic input failures from the dispatcher surface as HTTP 422."""
    fake_user = User(id="admin@bsvibe.dev", is_service=True, scope=["*"])

    monkeypatch.setattr(
        "backend.src.mcp.api.verify_bootstrap_token",
        lambda *_a, **_kw: fake_user,
    )
    monkeypatch.setattr("backend.src.mcp.api._authz_settings", lambda: MagicMock())

    # ``bsnexus_decisions_lock`` requires ``decision_id``; sending an
    # empty body trips the input_schema validator.
    res = await client.post(
        "/mcp/admin/tools/bsnexus_decisions_lock",
        json={},
        headers={"Authorization": "Bearer bsv_admin_test"},
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_admin_call_tool_scope_denied_returns_403(client, monkeypatch: pytest.MonkeyPatch) -> None:
    """A bootstrap caller with a narrow scope set is rejected by the
    dispatcher's scope gate; the transport surfaces 403 (not 401).
    """
    narrow = User(id="caller", is_service=True, scope=["bsnexus:projects:read"])

    monkeypatch.setattr(
        "backend.src.mcp.api.verify_bootstrap_token",
        lambda *_a, **_kw: narrow,
    )
    monkeypatch.setattr("backend.src.mcp.api._authz_settings", lambda: MagicMock())

    # ``bsnexus_decisions_lock`` requires ``bsnexus:decisions:write``.
    res = await client.post(
        "/mcp/admin/tools/bsnexus_decisions_lock",
        json={"decision_id": "abc", "resolution": "approved"},
        headers={"Authorization": "Bearer bsv_admin_test"},
    )
    assert res.status_code == 403
