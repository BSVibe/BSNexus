"""Part B / PR 4 — ToolRegistry sandbox-backed execution path.

When a ``SandboxSession`` is supplied, shell_exec and the file tools
route through it (``docker exec`` inside the project sandbox) instead
of host subprocesses. The denylist still runs in front. Host-path
behaviour (no session) is covered by test_tool_registry.py.
"""

from __future__ import annotations

import pytest

from backend.src.core.sandbox import SandboxError, SandboxResult
from backend.src.core.tools import SHELL_TIMEOUT_S, ToolError, ToolRegistry


class FakeSandboxSession:
    """Records routing; canned results."""

    def __init__(self) -> None:
        self.exec_calls: list[tuple[str, float, bool]] = []
        self.exec_result = SandboxResult(exit_code=0, stdout="hello", stderr="", timed_out=False)
        self.files: dict[str, bytes] = {}

    @property
    def workspace_mount(self) -> str:
        return "/work"

    async def exec(self, command: str, *, timeout_s: float, shell: bool = False) -> SandboxResult:
        self.exec_calls.append((command, timeout_s, shell))
        return self.exec_result

    async def read_file(self, rel_path: str, max_bytes: int) -> bytes:
        if rel_path not in self.files:
            raise SandboxError(f"read_file: not found: {rel_path}")
        return self.files[rel_path][:max_bytes]

    async def write_file(self, rel_path: str, content: bytes) -> None:
        self.files[rel_path] = content

    async def list_dir(self, rel_path: str) -> list[str]:
        return sorted(self.files)


async def test_shell_exec_routes_through_sandbox_session(tmp_path) -> None:
    fake = FakeSandboxSession()
    registry = ToolRegistry(workspace_dir=tmp_path, sandbox=fake)
    out = await registry.invoke("shell_exec", {"command": "echo hello"})
    assert len(fake.exec_calls) == 1
    command, timeout_s, shell = fake.exec_calls[0]
    assert command == "echo hello"
    assert shell is True
    assert timeout_s == SHELL_TIMEOUT_S
    assert "exit=0" in out
    assert "hello" in out


async def test_shell_exec_denylist_runs_before_sandbox(tmp_path) -> None:
    fake = FakeSandboxSession()
    registry = ToolRegistry(workspace_dir=tmp_path, sandbox=fake)
    with pytest.raises(ToolError, match="denylist"):
        await registry.invoke("shell_exec", {"command": "rm -rf /"})
    assert fake.exec_calls == []  # refused before reaching the sandbox


async def test_shell_exec_sandbox_timeout_raises(tmp_path) -> None:
    fake = FakeSandboxSession()
    fake.exec_result = SandboxResult(exit_code=None, stdout="", stderr="", timed_out=True)
    registry = ToolRegistry(workspace_dir=tmp_path, sandbox=fake)
    with pytest.raises(ToolError, match="timed out"):
        await registry.invoke("shell_exec", {"command": "sleep 99"})


async def test_file_write_then_read_route_through_sandbox(tmp_path) -> None:
    fake = FakeSandboxSession()
    registry = ToolRegistry(workspace_dir=tmp_path, sandbox=fake)
    await registry.invoke("file_write", {"path": "pkg/app.py", "content": "print('hi')\n"})
    assert fake.files["pkg/app.py"] == b"print('hi')\n"
    # nothing written to the host workspace
    assert not (tmp_path / "pkg" / "app.py").exists()

    content = await registry.invoke("file_read", {"path": "pkg/app.py"})
    assert content == "print('hi')\n"


async def test_file_list_routes_through_sandbox(tmp_path) -> None:
    fake = FakeSandboxSession()
    fake.files = {"a.py": b"", "b.py": b""}
    registry = ToolRegistry(workspace_dir=tmp_path, sandbox=fake)
    listed = await registry.invoke("file_list", {"path": "."})
    assert "a.py" in listed
    assert "b.py" in listed


async def test_file_read_missing_in_sandbox_raises_tool_error(tmp_path) -> None:
    fake = FakeSandboxSession()
    registry = ToolRegistry(workspace_dir=tmp_path, sandbox=fake)
    with pytest.raises(ToolError, match="file_read"):
        await registry.invoke("file_read", {"path": "nope.py"})
