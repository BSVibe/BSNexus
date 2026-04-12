"""Tests for tools/handler.py — ToolHandler execution and approval."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from backend.src.tools.approval import ApprovalMiddleware
from backend.src.tools.base import (
    Tool,
    ToolCall,
    ToolContext,
    ToolExecutionError,
    ToolResult,
)
from backend.src.tools.handler import ToolHandler


class EchoTool(Tool):
    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echoes input"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"text": {"type": "string"}}}

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str:
        return input.get("text", "")


class FailTool(Tool):
    @property
    def name(self) -> str:
        return "fail"

    @property
    def description(self) -> str:
        return "Always fails"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object"}

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str:
        raise ToolExecutionError("Expected failure")


class CrashTool(Tool):
    @property
    def name(self) -> str:
        return "crash"

    @property
    def description(self) -> str:
        return "Unexpected crash"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object"}

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str:
        raise RuntimeError("Unexpected")


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        project_id=uuid.uuid4(),
        workspace_path=tmp_path,
        workspace_type="server_managed",
        agent_id=uuid.uuid4(),
        agent_name="Agent",
        tenant_id=uuid.uuid4(),
        db_session_factory=AsyncMock(),
    )


class TestToolHandler:
    @pytest.mark.asyncio
    async def test_execute_single_success(self, ctx: ToolContext) -> None:
        handler = ToolHandler([EchoTool()], ctx)
        call = ToolCall(id="c1", name="echo", input={"text": "hi"})
        result = await handler.execute_single(call)
        assert result.content == "hi"
        assert not result.is_error

    @pytest.mark.asyncio
    async def test_execute_single_unknown_tool(self, ctx: ToolContext) -> None:
        handler = ToolHandler([EchoTool()], ctx)
        call = ToolCall(id="c1", name="nonexistent", input={})
        result = await handler.execute_single(call)
        assert result.is_error
        assert "Unknown tool" in result.content

    @pytest.mark.asyncio
    async def test_execute_single_expected_error(self, ctx: ToolContext) -> None:
        handler = ToolHandler([FailTool()], ctx)
        call = ToolCall(id="c1", name="fail", input={})
        result = await handler.execute_single(call)
        assert result.is_error
        assert "Expected failure" in result.content

    @pytest.mark.asyncio
    async def test_execute_single_unexpected_error(self, ctx: ToolContext) -> None:
        handler = ToolHandler([CrashTool()], ctx)
        call = ToolCall(id="c1", name="crash", input={})
        result = await handler.execute_single(call)
        assert result.is_error
        assert "Internal error" in result.content

    @pytest.mark.asyncio
    async def test_execute_batch(self, ctx: ToolContext) -> None:
        handler = ToolHandler([EchoTool()], ctx)
        calls = [
            ToolCall(id="c1", name="echo", input={"text": "a"}),
            ToolCall(id="c2", name="echo", input={"text": "b"}),
        ]
        results = await handler.execute_batch(calls)
        assert len(results) == 2
        assert results[0].content == "a"
        assert results[1].content == "b"

    def test_tool_names(self, ctx: ToolContext) -> None:
        handler = ToolHandler([EchoTool(), FailTool()], ctx)
        assert handler.tool_names == ["echo", "fail"]

    @pytest.mark.asyncio
    async def test_approval_middleware_intercept(self, ctx: ToolContext) -> None:
        approval = AsyncMock(spec=ApprovalMiddleware)
        approval.intercept = AsyncMock(
            return_value=ToolResult(tool_call_id="c1", content="intercepted")
        )
        handler = ToolHandler([EchoTool()], ctx, approval=approval)
        call = ToolCall(id="c1", name="echo", input={"text": "hi"})
        result = await handler.execute_single(call)
        assert result.content == "intercepted"
        approval.intercept.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_approval_middleware_passthrough(self, ctx: ToolContext) -> None:
        approval = AsyncMock(spec=ApprovalMiddleware)
        approval.intercept = AsyncMock(return_value=None)
        handler = ToolHandler([EchoTool()], ctx, approval=approval)
        call = ToolCall(id="c1", name="echo", input={"text": "hi"})
        result = await handler.execute_single(call)
        assert result.content == "hi"  # Tool executed normally
