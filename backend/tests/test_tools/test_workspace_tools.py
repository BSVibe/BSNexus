"""Tests for tools/workspace_tools.py — file read, write, list."""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from backend.src.tools.base import ToolContext, ToolExecutionError
from backend.src.tools.workspace_tools import FileReadTool, FileWriteTool, ListFilesTool


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


class TestFileReadTool:
    @pytest.mark.asyncio
    async def test_read_existing_file(self, ctx: ToolContext) -> None:
        (ctx.workspace_path / "hello.txt").write_text("line1\nline2\nline3\n")
        tool = FileReadTool()
        result = await tool.execute({"path": "hello.txt"}, ctx)
        assert "line1" in result
        assert "line3" in result

    @pytest.mark.asyncio
    async def test_read_with_offset_limit(self, ctx: ToolContext) -> None:
        (ctx.workspace_path / "data.txt").write_text("a\nb\nc\nd\ne\n")
        tool = FileReadTool()
        result = await tool.execute({"path": "data.txt", "offset": 1, "limit": 2}, ctx)
        assert result.strip() == "b\nc"

    @pytest.mark.asyncio
    async def test_read_missing_file(self, ctx: ToolContext) -> None:
        tool = FileReadTool()
        with pytest.raises(ToolExecutionError, match="File not found"):
            await tool.execute({"path": "nope.txt"}, ctx)

    @pytest.mark.asyncio
    async def test_path_traversal_blocked(self, ctx: ToolContext) -> None:
        tool = FileReadTool()
        with pytest.raises(ToolExecutionError, match="Path traversal"):
            await tool.execute({"path": "../../etc/passwd"}, ctx)


class TestFileWriteTool:
    @pytest.mark.asyncio
    async def test_write_creates_file(self, ctx: ToolContext) -> None:
        tool = FileWriteTool()
        result = await tool.execute({"path": "output.txt", "content": "hello"}, ctx)
        assert "Written" in result
        assert (ctx.workspace_path / "output.txt").read_text() == "hello"

    @pytest.mark.asyncio
    async def test_write_creates_directories(self, ctx: ToolContext) -> None:
        tool = FileWriteTool()
        await tool.execute({"path": "sub/dir/file.txt", "content": "nested"}, ctx)
        assert (ctx.workspace_path / "sub" / "dir" / "file.txt").read_text() == "nested"

    @pytest.mark.asyncio
    async def test_write_path_traversal_blocked(self, ctx: ToolContext) -> None:
        tool = FileWriteTool()
        with pytest.raises(ToolExecutionError, match="Path traversal"):
            await tool.execute({"path": "../escape.txt", "content": "bad"}, ctx)


class TestListFilesTool:
    @pytest.mark.asyncio
    async def test_list_root(self, ctx: ToolContext) -> None:
        (ctx.workspace_path / "a.txt").write_text("a")
        (ctx.workspace_path / "b.txt").write_text("b")
        (ctx.workspace_path / "sub").mkdir()

        tool = ListFilesTool()
        result = await tool.execute({}, ctx)
        assert "[DIR]" in result
        assert "a.txt" in result
        assert "b.txt" in result

    @pytest.mark.asyncio
    async def test_list_subdirectory(self, ctx: ToolContext) -> None:
        sub = ctx.workspace_path / "src"
        sub.mkdir()
        (sub / "main.py").write_text("print('hi')")

        tool = ListFilesTool()
        result = await tool.execute({"path": "src"}, ctx)
        assert "main.py" in result

    @pytest.mark.asyncio
    async def test_list_empty_dir(self, ctx: ToolContext) -> None:
        tool = ListFilesTool()
        result = await tool.execute({}, ctx)
        assert "empty" in result.lower()

    @pytest.mark.asyncio
    async def test_list_recursive(self, ctx: ToolContext) -> None:
        sub = ctx.workspace_path / "a" / "b"
        sub.mkdir(parents=True)
        (sub / "deep.txt").write_text("deep")

        tool = ListFilesTool()
        result = await tool.execute({"recursive": True}, ctx)
        assert "deep.txt" in result
