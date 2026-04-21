"""Tests for tools/shell_tools.py — ShellExecTool."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from backend.src.tools.base import ToolContext, ToolExecutionError


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


class TestShellExecTool:
    """Real shell execution inside the project workspace."""

    @pytest.mark.asyncio
    async def test_basic_echo(self, ctx: ToolContext) -> None:
        from backend.src.tools.shell_tools import ShellExecTool

        tool = ShellExecTool()
        result = json.loads(await tool.execute(
            {"command": "echo hello_world"}, ctx,
        ))
        assert result["exit_code"] == 0
        assert "hello_world" in result["stdout"]
        assert result["stderr"] == ""
        assert result["duration_s"] >= 0

    @pytest.mark.asyncio
    async def test_nonzero_exit_preserved(self, ctx: ToolContext) -> None:
        """A failing command must still return structured result; the
        agent needs to see failures as evidence of 'did not work'."""
        from backend.src.tools.shell_tools import ShellExecTool

        tool = ShellExecTool()
        result = json.loads(await tool.execute(
            {"command": "false"}, ctx,
        ))
        assert result["exit_code"] != 0

    @pytest.mark.asyncio
    async def test_cwd_is_workspace(self, ctx: ToolContext) -> None:
        """Commands run inside the project workspace — so file presence
        checks (`test -f foo`) work with workspace-relative paths."""
        from backend.src.tools.shell_tools import ShellExecTool

        (ctx.workspace_path / "marker.txt").write_text("present")

        tool = ShellExecTool()
        result = json.loads(await tool.execute(
            {"command": "test -f marker.txt && cat marker.txt"}, ctx,
        ))
        assert result["exit_code"] == 0
        assert "present" in result["stdout"]

    @pytest.mark.asyncio
    async def test_timeout_kills_runaway(self, ctx: ToolContext) -> None:
        from backend.src.tools.shell_tools import ShellExecTool

        tool = ShellExecTool()
        result = json.loads(await tool.execute(
            {"command": "sleep 5", "timeout_s": 1}, ctx,
        ))
        assert result["timed_out"] is True
        assert result["exit_code"] != 0

    @pytest.mark.asyncio
    async def test_stdout_truncation(self, ctx: ToolContext) -> None:
        """Large outputs are truncated with a marker so the agent's
        prompt doesn't balloon. Truncation must be visible."""
        from backend.src.tools.shell_tools import ShellExecTool

        tool = ShellExecTool()
        # Emit ~50KB — should get capped at the per-stream limit.
        result = json.loads(await tool.execute(
            {"command": "yes 'x' 2>/dev/null | head -c 50000"}, ctx,
        ))
        assert result["exit_code"] == 0
        # Default cap is 10KB per stream.
        assert len(result["stdout"]) <= 10_500
        assert result["truncated"] is True

    @pytest.mark.asyncio
    async def test_empty_command_rejected(self, ctx: ToolContext) -> None:
        from backend.src.tools.shell_tools import ShellExecTool

        tool = ShellExecTool()
        with pytest.raises(ToolExecutionError, match="empty|blank|required"):
            await tool.execute({"command": "   "}, ctx)

    @pytest.mark.asyncio
    async def test_timeout_clamped(self, ctx: ToolContext) -> None:
        """Agent can't request unbounded timeout — clamp at 300s."""
        from backend.src.tools.shell_tools import ShellExecTool

        tool = ShellExecTool()
        result = json.loads(await tool.execute(
            {"command": "echo ok", "timeout_s": 99999}, ctx,
        ))
        # Runs fine (echo is instant) — the important thing is the tool
        # accepted the input without raising. Internal clamping is
        # implementation-tested via `_effective_timeout`.
        assert result["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_stderr_captured(self, ctx: ToolContext) -> None:
        from backend.src.tools.shell_tools import ShellExecTool

        tool = ShellExecTool()
        result = json.loads(await tool.execute(
            {"command": "echo warning >&2; echo ok"}, ctx,
        ))
        assert "ok" in result["stdout"]
        assert "warning" in result["stderr"]
        assert result["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_workspace_autocreate(self, tmp_path: Path) -> None:
        """If the workspace dir doesn't exist on disk yet, the tool
        creates it — avoids a spurious failure on a fresh project."""
        from backend.src.tools.shell_tools import ShellExecTool

        missing = tmp_path / "newly_created"
        assert not missing.exists()

        ctx = ToolContext(
            project_id=uuid.uuid4(),
            workspace_path=missing,
            workspace_type="server_managed",
            agent_id=uuid.uuid4(),
            agent_name="A",
            tenant_id=uuid.uuid4(),
            db_session_factory=AsyncMock(),
        )
        tool = ShellExecTool()
        result = json.loads(await tool.execute(
            {"command": "pwd"}, ctx,
        ))
        assert result["exit_code"] == 0
        assert missing.exists()
