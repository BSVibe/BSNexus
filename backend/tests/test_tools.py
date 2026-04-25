"""Tool handlers invoked by the orchestrator adapter during a run."""

from __future__ import annotations

import json
import uuid

import pytest

from backend.src.core.tools import (
    MAX_FILE_BYTES,
    ToolRunLog,
    execute_tool_call,
    tool_schemas,
)


@pytest.fixture
def project_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def log(project_id: uuid.UUID) -> ToolRunLog:
    return ToolRunLog(project_id=project_id)


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path, monkeypatch):
    """Redirect the shared workspace root to a tmpdir per test."""
    monkeypatch.setattr(
        "backend.src.core.project_workspace._root",
        lambda: tmp_path,
    )
    return tmp_path


@pytest.mark.asyncio
async def test_file_write_persists_and_records(isolated_workspace, log, project_id):
    payload = json.dumps({"path": "src/main.py", "content": "print('ok')", "language": "python"})
    result = await execute_tool_call(name="file_write", raw_arguments=payload, log=log)

    assert result.startswith("ok:")
    assert len(log.written) == 1
    entry = log.written[0]
    assert entry.path == "src/main.py"
    assert entry.language == "python"
    assert entry.size == len("print('ok')".encode("utf-8"))
    # The file actually lives on disk.
    on_disk = isolated_workspace / str(project_id) / "src" / "main.py"
    assert on_disk.read_text(encoding="utf-8") == "print('ok')"


@pytest.mark.asyncio
async def test_file_read_returns_content(isolated_workspace, log, project_id):
    root = isolated_workspace / str(project_id)
    root.mkdir(parents=True, exist_ok=True)
    (root / "note.txt").write_text("hello", encoding="utf-8")

    result = await execute_tool_call(name="file_read", raw_arguments=json.dumps({"path": "note.txt"}), log=log)
    assert result == "hello"


@pytest.mark.asyncio
async def test_file_read_missing_file_returns_error_string(log):
    result = await execute_tool_call(name="file_read", raw_arguments=json.dumps({"path": "nope.txt"}), log=log)
    assert result.startswith("error:")
    # Tool-level errors should be reported to the model, not recorded
    # as a "written" file.
    assert log.written == []


@pytest.mark.asyncio
async def test_file_list_enumerates_workspace(isolated_workspace, log, project_id):
    root = isolated_workspace / str(project_id)
    root.mkdir(parents=True, exist_ok=True)
    (root / "a.txt").write_text("a", encoding="utf-8")
    (root / "b.txt").write_text("bb", encoding="utf-8")

    result = await execute_tool_call(name="file_list", raw_arguments="{}", log=log)
    assert "a.txt\t1" in result
    assert "b.txt\t2" in result


@pytest.mark.asyncio
async def test_file_write_rejects_escape_paths(log):
    payload = json.dumps({"path": "../outside.txt", "content": "x"})
    result = await execute_tool_call(name="file_write", raw_arguments=payload, log=log)
    assert result.startswith("error:")
    assert log.errors == 1
    assert log.written == []


@pytest.mark.asyncio
async def test_file_write_rejects_oversize_content(log):
    big = "x" * (MAX_FILE_BYTES + 1)
    payload = json.dumps({"path": "big.txt", "content": big})
    result = await execute_tool_call(name="file_write", raw_arguments=payload, log=log)
    assert result.startswith("error:")
    assert log.written == []


@pytest.mark.asyncio
async def test_invalid_json_arguments_return_error(log):
    result = await execute_tool_call(name="file_write", raw_arguments="{not json", log=log)
    assert result.startswith("error:")
    assert log.errors == 1


@pytest.mark.asyncio
async def test_unknown_tool_returns_error(log):
    result = await execute_tool_call(name="ping", raw_arguments="{}", log=log)
    assert result.startswith("error:")


def test_tool_schemas_filter_by_name():
    schemas = tool_schemas(["file_write"])
    assert [s["function"]["name"] for s in schemas] == ["file_write"]

    all_schemas = tool_schemas()
    assert {s["function"]["name"] for s in all_schemas} == {
        "file_write",
        "file_read",
        "file_list",
        "shell_exec",
    }


@pytest.mark.asyncio
async def test_shell_exec_captures_stdout_and_exit_zero(isolated_workspace, log):
    payload = json.dumps({"command": "echo hello && echo world"})
    result = await execute_tool_call(name="shell_exec", raw_arguments=payload, log=log)
    assert result.startswith("exit=0")
    assert "hello" in result
    assert "world" in result
    assert len(log.shells) == 1
    assert log.shells[0].exit_code == 0


@pytest.mark.asyncio
async def test_shell_exec_nonzero_exit_returned_as_plain_payload(isolated_workspace, log):
    payload = json.dumps({"command": "false"})
    result = await execute_tool_call(name="shell_exec", raw_arguments=payload, log=log)
    assert result.startswith("exit=1")
    assert log.shells[0].exit_code == 1
    # Nonzero exit is NOT an error from the tool's perspective — it's
    # data the model needs to react to.
    assert log.errors == 0


@pytest.mark.asyncio
async def test_shell_exec_runs_with_workspace_as_cwd(isolated_workspace, log, project_id):
    # Create a marker file in the workspace.
    workspace_root = isolated_workspace / str(project_id)
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "marker.txt").write_text("hi", encoding="utf-8")

    payload = json.dumps({"command": "cat marker.txt"})
    result = await execute_tool_call(name="shell_exec", raw_arguments=payload, log=log)
    assert result.startswith("exit=0")
    assert "hi" in result


@pytest.mark.asyncio
async def test_shell_exec_honors_timeout(isolated_workspace, log):
    payload = json.dumps({"command": "sleep 5", "timeout_s": 1})
    result = await execute_tool_call(name="shell_exec", raw_arguments=payload, log=log)
    assert result.startswith("exit=-1")
    assert "timeout" in result.lower()
    assert log.shells[0].exit_code == -1


@pytest.mark.asyncio
async def test_shell_exec_truncates_runaway_output(isolated_workspace, log):
    # Emit ~30KB of output; should clip at SHELL_MAX_OUTPUT_BYTES.
    payload = json.dumps({"command": "python3 -c 'print(\"x\" * 30000)'"})
    result = await execute_tool_call(name="shell_exec", raw_arguments=payload, log=log)
    assert "truncated" in result
    assert len(result.encode("utf-8")) < 25_000
