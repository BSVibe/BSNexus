"""PR11 — recover misnamed local tool calls.

qwen3-coder dogfood (PR11 iter 3) showed the LLM consolidates to a
single tool name (``shell_exec``) but emits arguments shaped for a
different tool (``{"path": "...", "content": "..."}``). The dispatcher
runs the JSON as shell, fails, loops, and eats the round budget.

Recovery rule: when the args are *unambiguously* shaped for another
known local tool, rewrite the name. Never guess on ambiguous args.
"""

from __future__ import annotations

from backend.src.core.tools import recover_misnamed_local_tool


def test_shell_exec_with_path_and_content_recovers_to_file_write() -> None:
    """The exact PR11 iter-3 pattern: ``shell_exec`` + ``{path, content}``
    with no ``command`` is unambiguously a file_write."""
    name, note = recover_misnamed_local_tool(
        "shell_exec", {"path": "add.py", "content": "def add(a, b):\n    return a + b"}
    )
    assert name == "file_write"
    assert note is not None and "file_write" in note


def test_shell_exec_with_path_only_recovers_to_file_read() -> None:
    name, note = recover_misnamed_local_tool("shell_exec", {"path": "add.py"})
    assert name == "file_read"
    assert note is not None and "file_read" in note


def test_shell_exec_with_command_is_not_recovered() -> None:
    """Real shell_exec call must pass through unchanged."""
    name, note = recover_misnamed_local_tool("shell_exec", {"command": "python -m pytest tests/test_add.py -q"})
    assert name == "shell_exec"
    assert note is None


def test_file_write_with_command_recovers_to_shell_exec() -> None:
    """Inverse mistake: name=file_write but args={command} → shell_exec."""
    name, note = recover_misnamed_local_tool("file_write", {"command": "ls -la"})
    assert name == "shell_exec"
    assert note is not None


def test_file_write_with_path_and_content_passes_through() -> None:
    name, note = recover_misnamed_local_tool("file_write", {"path": "x.py", "content": "x = 1"})
    assert name == "file_write"
    assert note is None


def test_ambiguous_args_no_recovery() -> None:
    """Empty / unrelated args → leave name alone, let the handler error."""
    name, note = recover_misnamed_local_tool("shell_exec", {})
    assert name == "shell_exec"
    assert note is None


def test_unknown_tool_name_passes_through() -> None:
    """Non-local tool name (MCP) — never touch."""
    name, note = recover_misnamed_local_tool("kb_search", {"query": "anything"})
    assert name == "kb_search"
    assert note is None


def test_empty_content_string_still_recovers_file_write() -> None:
    """``file_write`` with empty content is legal (touch a file).
    Recovery must accept ``content == ""``."""
    name, note = recover_misnamed_local_tool("shell_exec", {"path": "empty.py", "content": ""})
    assert name == "file_write"
    assert note is not None


def test_command_with_whitespace_only_treated_as_no_command() -> None:
    """``shell_exec`` with ``command=" "`` and ``path+content`` should
    still recover — whitespace command is not a real command."""
    name, _ = recover_misnamed_local_tool("shell_exec", {"command": "  ", "path": "x.py", "content": "y = 2"})
    assert name == "file_write"
