"""PR10 — DirectLLMAdapter dispatches built-in tools locally, MCP tools via session.

Pre-PR10: the only tool surface the LLM saw was the MCP server's 6
tools (decision/artifact/knowledge/deliverable_report). file_write
and shell_exec were ABSENT from MCP — the LLM was hallucinating
those names and somehow files appeared (PR9 D investigation noted
this gap).

PR10 fix: ``DirectLLMAdapter`` advertises ``file_write`` /
``file_read`` / ``file_list`` / ``shell_exec`` as LOCAL tools
(executed in-process via ``backend.src.core.tools.execute_tool_call``
against the project workspace) AND MCP tools as remote. This
mirrors claude-code's architecture: filesystem / shell are local;
domain-extension tools (decisions / knowledge) are MCP.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.src.core.llm.direct_client import DirectLLMAdapter


def _delta_chunk(content: str = "", tool_calls: list[dict] | None = None, finish_reason: str | None = None) -> Any:
    delta = MagicMock()
    delta.content = content if content else None
    if tool_calls:
        out = []
        for tc in tool_calls:
            mock_tc = MagicMock()
            mock_tc.index = tc.get("index", 0)
            mock_tc.id = tc.get("id")
            fn = MagicMock()
            fn.name = tc.get("function_name")
            fn.arguments = tc.get("function_arguments")
            mock_tc.function = fn
            out.append(mock_tc)
        delta.tool_calls = out
    else:
        delta.tool_calls = None
    choice = MagicMock()
    choice.delta = delta
    choice.finish_reason = finish_reason
    chunk = MagicMock()
    chunk.choices = [choice]
    return chunk


async def _async_iter(items: list[Any]):
    for item in items:
        yield item


@pytest.mark.asyncio
async def test_file_write_is_dispatched_locally_not_via_mcp(tmp_path, monkeypatch) -> None:
    """LLM emits ``file_write`` → adapter runs the LOCAL handler
    (writes to project workspace) instead of forwarding to the MCP
    session. Verifies file exists on disk after the call."""
    project_id = uuid.uuid4()
    # Redirect project workspace root to tmp_path so the test doesn't
    # touch the real workspace dir.
    from backend.src.core import project_workspace

    monkeypatch.setattr(project_workspace, "_root", lambda: tmp_path)

    adapter = DirectLLMAdapter(
        model="ollama_chat/qwen3-coder:30b",
        api_key="k",
        project_id=project_id,
    )

    # MCP session that would FAIL if called for file_write — proves we
    # didn't take the MCP path.
    session = AsyncMock()
    list_result = MagicMock()
    list_result.tools = []
    session.list_tools = AsyncMock(return_value=list_result)
    session.call_tool = AsyncMock(side_effect=AssertionError("file_write must NOT go via MCP"))

    round1 = _async_iter(
        [
            _delta_chunk(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "c1",
                        "function_name": "file_write",
                        "function_arguments": '{"path": "add.py", "content": "def add(a,b): return a+b\\n"}',
                    }
                ]
            ),
            _delta_chunk(finish_reason="tool_calls"),
        ]
    )
    round2 = _async_iter(
        [
            _delta_chunk(content="done"),
            _delta_chunk(finish_reason="stop"),
        ]
    )

    @asynccontextmanager
    async def _session_ctx():
        yield session

    with (
        patch.object(adapter, "_mcp_session", _session_ctx),
        patch(
            "backend.src.core.llm.direct_client.acompletion",
            AsyncMock(side_effect=[round1, round2]),
        ),
    ):
        result = await adapter.execute(system_prompt="s", user_prompt="u", tools_allowed=[])

    # File actually landed on disk.
    written = (project_workspace.project_workspace_path(project_id) / "add.py").read_text()
    assert "def add(a,b): return a+b" in written
    # MCP session.call_tool was NEVER called for file_write.
    session.call_tool.assert_not_awaited()
    # Result includes the local-tool log so publish_run_output can
    # auto-derive a verification block from observed shell invocations.
    assert "local_tool_log" in result
    assert any(w["path"] == "add.py" for w in result["local_tool_log"]["written_files"])


@pytest.mark.asyncio
async def test_unknown_tool_falls_through_to_mcp(tmp_path, monkeypatch) -> None:
    """A tool name NOT in the local set (e.g. ``decision_create``)
    forwards to the MCP session as before."""
    project_id = uuid.uuid4()
    from backend.src.core import project_workspace

    monkeypatch.setattr(project_workspace, "_root", lambda: tmp_path)

    adapter = DirectLLMAdapter(
        model="ollama_chat/qwen3-coder:30b",
        api_key="k",
        project_id=project_id,
    )

    session = AsyncMock()
    list_result = MagicMock()
    list_result.tools = []
    session.list_tools = AsyncMock(return_value=list_result)

    async def _call_tool(name: str, args: dict) -> Any:
        out = MagicMock()
        out.content = [MagicMock(text=f"mcp ran {name}")]
        return out

    session.call_tool = AsyncMock(side_effect=_call_tool)

    round1 = _async_iter(
        [
            _delta_chunk(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "c1",
                        "function_name": "decision_create",
                        "function_arguments": '{"question": "?"}',
                    }
                ]
            ),
            _delta_chunk(finish_reason="tool_calls"),
        ]
    )
    round2 = _async_iter(
        [
            _delta_chunk(content="done"),
            _delta_chunk(finish_reason="stop"),
        ]
    )

    @asynccontextmanager
    async def _session_ctx():
        yield session

    with (
        patch.object(adapter, "_mcp_session", _session_ctx),
        patch(
            "backend.src.core.llm.direct_client.acompletion",
            AsyncMock(side_effect=[round1, round2]),
        ),
    ):
        await adapter.execute(system_prompt="s", user_prompt="u", tools_allowed=[])

    # MCP session.call_tool WAS called for decision_create.
    session.call_tool.assert_awaited_once_with("decision_create", {"question": "?"})


@pytest.mark.asyncio
async def test_shell_exec_local_dispatch_records_invocation(tmp_path, monkeypatch) -> None:
    """``shell_exec`` runs locally; the ToolRunLog records the command,
    exit code, and duration so publish_run_output can auto-derive a
    verification block from the most recent successful invocation."""
    project_id = uuid.uuid4()
    from backend.src.core import project_workspace

    monkeypatch.setattr(project_workspace, "_root", lambda: tmp_path)
    # Ensure workspace dir exists (shell_exec cwd's into it).
    project_workspace.project_workspace_path(project_id).mkdir(parents=True, exist_ok=True)

    adapter = DirectLLMAdapter(
        model="ollama_chat/qwen3-coder:30b",
        api_key="k",
        project_id=project_id,
    )

    session = AsyncMock()
    list_result = MagicMock()
    list_result.tools = []
    session.list_tools = AsyncMock(return_value=list_result)
    session.call_tool = AsyncMock(side_effect=AssertionError("shell_exec must NOT go via MCP"))

    round1 = _async_iter(
        [
            _delta_chunk(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "c1",
                        "function_name": "shell_exec",
                        "function_arguments": '{"command": "echo hello", "timeout_s": 5}',
                    }
                ]
            ),
            _delta_chunk(finish_reason="tool_calls"),
        ]
    )
    round2 = _async_iter(
        [
            _delta_chunk(content="ran"),
            _delta_chunk(finish_reason="stop"),
        ]
    )

    @asynccontextmanager
    async def _session_ctx():
        yield session

    with (
        patch.object(adapter, "_mcp_session", _session_ctx),
        patch(
            "backend.src.core.llm.direct_client.acompletion",
            AsyncMock(side_effect=[round1, round2]),
        ),
    ):
        result = await adapter.execute(system_prompt="s", user_prompt="u", tools_allowed=[])

    shells = result["local_tool_log"]["shell_invocations"]
    assert len(shells) == 1
    assert shells[0]["command"] == "echo hello"
    assert shells[0]["exit_code"] == 0
    session.call_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_shell_exec_with_file_write_args_recovers_to_file_write(tmp_path, monkeypatch) -> None:
    """PR11 — qwen3-coder:30b dogfood iter 3 emitted ``shell_exec``
    with ``{path, content}`` args (file_write payload under the wrong
    name). Adapter must recover the intent: write the file, record it
    in ``written_files``, do NOT shell-execute the JSON."""
    project_id = uuid.uuid4()
    from backend.src.core import project_workspace

    monkeypatch.setattr(project_workspace, "_root", lambda: tmp_path)

    adapter = DirectLLMAdapter(
        model="ollama_chat/qwen3-coder:30b",
        api_key="k",
        project_id=project_id,
    )

    session = AsyncMock()
    list_result = MagicMock()
    list_result.tools = []
    session.list_tools = AsyncMock(return_value=list_result)
    session.call_tool = AsyncMock(side_effect=AssertionError("recovered call must NOT go via MCP"))

    round1 = _async_iter(
        [
            _delta_chunk(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "c1",
                        "function_name": "shell_exec",
                        "function_arguments": '{"path": "add.py", "content": "def add(a, b):\\n    return a + b\\n"}',
                    }
                ]
            ),
            _delta_chunk(finish_reason="tool_calls"),
        ]
    )
    round2 = _async_iter(
        [
            _delta_chunk(content="wrote it"),
            _delta_chunk(finish_reason="stop"),
        ]
    )

    @asynccontextmanager
    async def _session_ctx():
        yield session

    with (
        patch.object(adapter, "_mcp_session", _session_ctx),
        patch(
            "backend.src.core.llm.direct_client.acompletion",
            AsyncMock(side_effect=[round1, round2]),
        ),
    ):
        result = await adapter.execute(system_prompt="s", user_prompt="u", tools_allowed=[])

    written = (project_workspace.project_workspace_path(project_id) / "add.py").read_text()
    assert "def add(a, b):" in written
    log = result["local_tool_log"]
    assert any(w["path"] == "add.py" for w in log["written_files"])
    # Crucially: NO shell invocation recorded — the misnamed call was
    # rerouted to file_write, not run as a JSON-payload shell command.
    assert log["shell_invocations"] == []
    session.call_tool.assert_not_awaited()
