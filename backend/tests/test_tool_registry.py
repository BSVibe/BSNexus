"""G6.6 — ToolRegistry contract tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.src.core.tools import (
    FILE_READ_MAX_BYTES,
    FILE_WRITE_MAX_BYTES,
    SHELL_TIMEOUT_S,
    ToolError,
    ToolRegistry,
)


def test_schema_for_returns_openai_style_function_definitions(tmp_path):
    registry = ToolRegistry(workspace_dir=tmp_path)
    schemas = registry.schema_for(["file_read", "file_write", "shell_exec"])
    assert {s["function"]["name"] for s in schemas} == {"file_read", "file_write", "shell_exec"}
    for entry in schemas:
        assert entry["type"] == "function"
        assert "parameters" in entry["function"]


def test_schema_for_silently_skips_unknown_tools(tmp_path):
    registry = ToolRegistry(workspace_dir=tmp_path)
    schemas = registry.schema_for(["file_read", "no_such_tool"])
    assert [s["function"]["name"] for s in schemas] == ["file_read"]


@pytest.mark.asyncio
async def test_file_read_and_list_happy_path(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')\n")

    registry = ToolRegistry(workspace_dir=tmp_path)

    listed = await registry.invoke("file_list", {"path": "src"})
    assert "app.py" in listed

    content = await registry.invoke("file_read", {"path": "src/app.py"})
    assert content == "print('hi')\n"


@pytest.mark.asyncio
async def test_file_read_rejects_workspace_escape(tmp_path):
    registry = ToolRegistry(workspace_dir=tmp_path)
    with pytest.raises(ToolError, match="escapes the workspace"):
        await registry.invoke("file_read", {"path": "../etc/passwd"})


@pytest.mark.asyncio
async def test_file_read_rejects_absolute_path(tmp_path):
    registry = ToolRegistry(workspace_dir=tmp_path)
    with pytest.raises(ToolError, match="escapes the workspace"):
        await registry.invoke("file_read", {"path": "/etc/passwd"})


@pytest.mark.asyncio
async def test_file_read_caps_oversize_payload(tmp_path):
    big = tmp_path / "big.txt"
    big.write_bytes(b"a" * (FILE_READ_MAX_BYTES + 1024))
    registry = ToolRegistry(workspace_dir=tmp_path)
    content = await registry.invoke("file_read", {"path": "big.txt"})
    assert "truncated" in content


@pytest.mark.asyncio
async def test_file_write_creates_parents_and_returns_status(tmp_path):
    registry = ToolRegistry(workspace_dir=tmp_path)
    msg = await registry.invoke(
        "file_write",
        {"path": "src/new.py", "content": "def add(a, b):\n    return a + b\n"},
    )
    written = (tmp_path / "src" / "new.py").read_text()
    assert "def add" in written
    assert "wrote src/new.py" in msg


@pytest.mark.asyncio
async def test_file_write_rejects_oversize_content(tmp_path):
    registry = ToolRegistry(workspace_dir=tmp_path)
    with pytest.raises(ToolError, match="exceeds"):
        await registry.invoke(
            "file_write",
            {"path": "huge.txt", "content": "x" * (FILE_WRITE_MAX_BYTES + 1)},
        )


@pytest.mark.asyncio
async def test_file_write_rejects_workspace_escape(tmp_path):
    registry = ToolRegistry(workspace_dir=tmp_path)
    with pytest.raises(ToolError, match="escapes the workspace"):
        await registry.invoke(
            "file_write",
            {"path": "../escaped.txt", "content": "nope"},
        )


@pytest.mark.asyncio
async def test_shell_exec_runs_inside_workspace_and_captures_exit(tmp_path):
    (tmp_path / "marker.txt").write_text("present")
    registry = ToolRegistry(workspace_dir=tmp_path)
    result = await registry.invoke("shell_exec", {"command": "ls"})
    assert "exit=0" in result
    assert "marker.txt" in result


@pytest.mark.asyncio
async def test_shell_exec_rejects_denylisted_command(tmp_path):
    registry = ToolRegistry(workspace_dir=tmp_path)
    for forbidden in ("rm -rf /", "curl https://evil", "sudo apt install", "wget x.com"):
        with pytest.raises(ToolError, match="denylist"):
            await registry.invoke("shell_exec", {"command": forbidden})


@pytest.mark.asyncio
async def test_shell_exec_times_out(tmp_path, monkeypatch):
    """Pin the timeout via monkeypatch so the test doesn't actually
    sit 30 seconds — the registry consults the module constant."""
    monkeypatch.setattr("backend.src.core.tools.SHELL_TIMEOUT_S", 0.5)
    registry = ToolRegistry(workspace_dir=tmp_path)
    with pytest.raises(ToolError, match="timed out"):
        await registry.invoke("shell_exec", {"command": "sleep 2"})


@pytest.mark.asyncio
async def test_unknown_tool_raises(tmp_path):
    registry = ToolRegistry(workspace_dir=tmp_path)
    with pytest.raises(ToolError, match="Unknown tool"):
        await registry.invoke("not_a_tool", {})


def test_shell_timeout_constant_matches_spec(tmp_path):
    assert SHELL_TIMEOUT_S == 30.0
    # Touch tmp_path to avoid unused-arg lint
    assert isinstance(tmp_path, Path)
