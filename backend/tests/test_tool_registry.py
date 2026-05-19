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


def test_declare_verification_description_tells_model_to_scope_to_changed_files(tmp_path):
    """G-F: a repo-wide lint/format contract (`ruff check .`) fails on
    pre-existing debt and provokes repo-wide collateral edits. The
    declare_verification tool must steer the work LLM to scope its
    commands to the changed paths."""
    registry = ToolRegistry(workspace_dir=tmp_path)
    schema = registry.schema_for(["declare_verification"])[0]
    description = schema["function"]["description"].lower()
    assert "scope" in description
    assert "whole repo" in description or "repo-wide" in description


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


# ───────────────────────────── file_edit ──────────────────────────────────


@pytest.mark.asyncio
async def test_file_edit_requires_prior_read(tmp_path):
    """A local model must edit against real content — file_edit refuses
    a path it has not file_read (or file_write) this attempt."""
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    registry = ToolRegistry(workspace_dir=tmp_path)
    with pytest.raises(ToolError, match="file_read .* before editing"):
        await registry.invoke(
            "file_edit",
            {"path": "calc.py", "old_string": "a + b", "new_string": "a - b"},
        )


@pytest.mark.asyncio
async def test_file_edit_surgical_replacement_after_read(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n\ndef noop():\n    pass\n")
    registry = ToolRegistry(workspace_dir=tmp_path)
    await registry.invoke("file_read", {"path": "calc.py"})
    msg = await registry.invoke(
        "file_edit",
        {"path": "calc.py", "old_string": "return a + b", "new_string": "return a - b"},
    )
    updated = (tmp_path / "calc.py").read_text()
    assert "return a - b" in updated
    assert "def noop():\n    pass" in updated  # untouched
    assert "1 replacement" in msg


@pytest.mark.asyncio
async def test_file_edit_grounded_by_file_write(tmp_path):
    """Writing a file also grounds it — the LLM supplied the content,
    so a follow-up edit needs no separate read."""
    registry = ToolRegistry(workspace_dir=tmp_path)
    await registry.invoke("file_write", {"path": "n.py", "content": "x = 1\n"})
    await registry.invoke("file_edit", {"path": "n.py", "old_string": "x = 1", "new_string": "x = 2"})
    assert (tmp_path / "n.py").read_text() == "x = 2\n"


@pytest.mark.asyncio
async def test_file_edit_old_string_not_found(tmp_path):
    (tmp_path / "f.py").write_text("hello\n")
    registry = ToolRegistry(workspace_dir=tmp_path)
    await registry.invoke("file_read", {"path": "f.py"})
    with pytest.raises(ToolError, match="not found"):
        await registry.invoke("file_edit", {"path": "f.py", "old_string": "goodbye", "new_string": "hi"})


@pytest.mark.asyncio
async def test_file_edit_non_unique_old_string_rejected(tmp_path):
    (tmp_path / "f.py").write_text("v = 0\nv = 0\n")
    registry = ToolRegistry(workspace_dir=tmp_path)
    await registry.invoke("file_read", {"path": "f.py"})
    with pytest.raises(ToolError, match="occurs 2"):
        await registry.invoke("file_edit", {"path": "f.py", "old_string": "v = 0", "new_string": "v = 1"})


@pytest.mark.asyncio
async def test_file_edit_replace_all(tmp_path):
    (tmp_path / "f.py").write_text("v = 0\nv = 0\n")
    registry = ToolRegistry(workspace_dir=tmp_path)
    await registry.invoke("file_read", {"path": "f.py"})
    msg = await registry.invoke(
        "file_edit",
        {"path": "f.py", "old_string": "v = 0", "new_string": "v = 1", "replace_all": True},
    )
    assert (tmp_path / "f.py").read_text() == "v = 1\nv = 1\n"
    assert "2 replacements" in msg


@pytest.mark.asyncio
async def test_file_edit_identical_strings_rejected(tmp_path):
    (tmp_path / "f.py").write_text("a\n")
    registry = ToolRegistry(workspace_dir=tmp_path)
    await registry.invoke("file_read", {"path": "f.py"})
    with pytest.raises(ToolError, match="identical"):
        await registry.invoke("file_edit", {"path": "f.py", "old_string": "a", "new_string": "a"})


@pytest.mark.asyncio
async def test_file_edit_in_work_phase_schema(tmp_path):
    registry = ToolRegistry(workspace_dir=tmp_path)
    names = {s["function"]["name"] for s in registry.schema_for(["file_edit"])}
    assert "file_edit" in names
