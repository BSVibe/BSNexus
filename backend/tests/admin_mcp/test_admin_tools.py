"""Round 4 BSNexus admin MCP — first-class tool dispatch tests."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from bsvibe_authz import User
from mcp.types import CallToolRequest, CallToolRequestParams, ListToolsRequest, ServerResult

from backend.src.admin_mcp.admin_tools import (
    EXPECTED_ADMIN_TOOL_NAMES,
    register_admin_tools,
)
from backend.src.admin_mcp.api import (
    ToolContext,
    ToolError,
    ToolRegistry,
)
from backend.src.admin_mcp.server import build_server


def _make_user(scopes: list[str] | None = None, tenant: UUID | None = None) -> User:
    return User(
        id="user-1",
        email="u@example.com",
        active_tenant_id=tenant,
        tenants=[],
        is_service=False,
        scope=["nexus:*"] if scopes is None else scopes,
    )


def _make_ctx() -> ToolContext:
    return ToolContext(user=_make_user())


class TestRegistry:
    def test_register_admin_tools_provides_all_expected_names(self) -> None:
        reg = ToolRegistry()
        lb: Any = AsyncMock()
        register_admin_tools(reg, lb)
        names = set(reg.names())
        for expected in EXPECTED_ADMIN_TOOL_NAMES:
            assert expected in names, f"missing tool: {expected}"
        assert len(names) == len(EXPECTED_ADMIN_TOOL_NAMES)


class TestProjectsList:
    @pytest.mark.asyncio
    async def test_calls_loopback_and_returns_payload(self) -> None:
        lb = AsyncMock(return_value=[{"id": "p1", "name": "alpha"}])
        reg = ToolRegistry()
        register_admin_tools(reg, lb)
        result = await reg.call_tool("bsnexus_projects_list", {"limit": 25}, _make_ctx())
        # Loopback called with GET /projects + params
        assert lb.await_count == 1
        call = lb.await_args
        assert call.args[1] == "GET"
        assert call.args[2] == "/projects"
        assert call.kwargs["params"] == {"limit": 25, "offset": 0}
        # Result echoes the REST payload verbatim through the RootModel envelope.
        assert result == [{"id": "p1", "name": "alpha"}]


class TestProjectsCreate:
    @pytest.mark.asyncio
    async def test_posts_body_and_returns_created_project(self) -> None:
        lb = AsyncMock(return_value={"id": "p1", "name": "alpha"})
        reg = ToolRegistry()
        register_admin_tools(reg, lb)
        result = await reg.call_tool(
            "bsnexus_projects_create",
            {"name": "alpha", "description": "hi"},
            _make_ctx(),
        )
        call = lb.await_args
        assert call.args[1] == "POST"
        assert call.args[2] == "/projects"
        assert call.kwargs["body"] == {"name": "alpha", "description": "hi"}
        assert result == {"id": "p1", "name": "alpha"}


class TestDecisionsResolve:
    @pytest.mark.asyncio
    async def test_locks_in_decision_with_resolution(self) -> None:
        lb = AsyncMock(return_value={"id": "d1", "status": "resolved"})
        reg = ToolRegistry()
        register_admin_tools(reg, lb)
        decision_id = uuid4()
        result = await reg.call_tool(
            "bsnexus_decisions_resolve",
            {"decision_id": str(decision_id), "resolution": "approved by founder"},
            _make_ctx(),
        )
        call = lb.await_args
        assert call.args[1] == "POST"
        assert call.args[2] == f"/decisions/{decision_id}/resolve"
        assert call.kwargs["body"] == {"resolution": "approved by founder"}
        assert result["status"] == "resolved"


class TestProjectsUpdateRequiresField:
    @pytest.mark.asyncio
    async def test_empty_patch_raises_invalid_input(self) -> None:
        lb = AsyncMock()
        reg = ToolRegistry()
        register_admin_tools(reg, lb)
        with pytest.raises(ToolError) as exc:
            await reg.call_tool(
                "bsnexus_projects_update",
                {"project_id": str(uuid4())},
                _make_ctx(),
            )
        assert exc.value.code == "invalid_input"
        # And loopback never gets called
        assert lb.await_count == 0


class TestScopeEnforcement:
    @pytest.mark.asyncio
    async def test_caller_without_scope_is_denied(self) -> None:
        lb = AsyncMock()
        reg = ToolRegistry()
        register_admin_tools(reg, lb)
        # Read tool with empty scope → permission_denied
        ctx = ToolContext(user=_make_user(scopes=[]))
        with pytest.raises(ToolError) as exc:
            await reg.call_tool("bsnexus_projects_list", {}, ctx)
        assert exc.value.code == "permission_denied"
        assert lb.await_count == 0


class TestServerCallToolWrapsInTextContent:
    """Round 4 F22 — the SDK's CallToolResult.content is `list[ContentBlock]`,
    not a free-form dict. The server adapter must wrap the registry's dict
    output in [TextContent]."""

    @pytest.mark.asyncio
    async def test_call_tool_wraps_dict_result_in_text_content(self) -> None:
        lb = AsyncMock(return_value={"id": "p1"})
        reg = ToolRegistry()
        register_admin_tools(reg, lb)

        async def _provider() -> ToolContext:
            return _make_ctx()

        server = build_server(reg, context_provider=_provider)
        handler = server.request_handlers[CallToolRequest]
        req = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="bsnexus_projects_create",
                arguments={"name": "alpha"},
            ),
        )
        result = await handler(req)
        assert isinstance(result, ServerResult)
        assert result.root.isError is False
        text_blocks = [c for c in result.root.content if c.type == "text"]
        assert text_blocks, "expected at least one text block"
        assert json.loads(text_blocks[0].text) == {"id": "p1"}

    @pytest.mark.asyncio
    async def test_list_tools_returns_mcp_tool_objects(self) -> None:
        lb = AsyncMock()
        reg = ToolRegistry()
        register_admin_tools(reg, lb)

        async def _provider() -> ToolContext:
            return _make_ctx()

        server = build_server(reg, context_provider=_provider)
        handler = server.request_handlers[ListToolsRequest]
        req = ListToolsRequest(method="tools/list")
        result = await handler(req)
        names = {t.name for t in result.root.tools}
        for expected in EXPECTED_ADMIN_TOOL_NAMES:
            assert expected in names
