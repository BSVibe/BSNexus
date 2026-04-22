"""Tests for tools/base.py — core types and Tool ABC."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from backend.src.tools.base import (
    Tool,
    ToolCall,
    ToolContext,
    ToolDefinition,
    ToolExecutionError,
    ToolResult,
)


class DummyTool(Tool):
    @property
    def name(self) -> str:
        return "dummy"

    @property
    def description(self) -> str:
        return "A dummy tool for testing"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"msg": {"type": "string"}}}

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str:
        return f"echo: {input.get('msg', '')}"


@pytest.fixture
def tool_context(tmp_path: Path) -> ToolContext:
    return ToolContext(
        project_id=uuid.uuid4(),
        workspace_path=tmp_path,
        workspace_type="server_managed",
        agent_id=uuid.uuid4(),
        agent_name="TestAgent",
        tenant_id=uuid.uuid4(),
        db_session_factory=AsyncMock(),
    )


class TestToolDefinition:
    def test_to_dict_format(self) -> None:
        td = ToolDefinition(
            name="test_tool",
            description="A test",
            input_schema={"type": "object"},
        )
        d = td.to_dict()
        assert d["type"] == "function"
        assert d["function"]["name"] == "test_tool"
        assert d["function"]["description"] == "A test"
        assert d["function"]["parameters"] == {"type": "object"}


class TestToolCall:
    def test_fields(self) -> None:
        tc = ToolCall(id="call-1", name="test", input={"a": 1})
        assert tc.id == "call-1"
        assert tc.name == "test"
        assert tc.input == {"a": 1}


class TestToolResult:
    def test_default_not_error(self) -> None:
        tr = ToolResult(tool_call_id="call-1", content="ok")
        assert not tr.is_error

    def test_error_result(self) -> None:
        tr = ToolResult(tool_call_id="call-1", content="fail", is_error=True)
        assert tr.is_error


class TestDummyTool:
    def test_to_definition(self) -> None:
        tool = DummyTool()
        defn = tool.to_definition()
        assert defn.name == "dummy"
        assert defn.description == "A dummy tool for testing"

    @pytest.mark.asyncio
    async def test_execute(self, tool_context: ToolContext) -> None:
        tool = DummyTool()
        result = await tool.execute({"msg": "hello"}, tool_context)
        assert result == "echo: hello"


class TestToolExecutionError:
    def test_message_preserved(self) -> None:
        err = ToolExecutionError("something went wrong")
        assert err.message == "something went wrong"
        assert "something went wrong" in str(err)
