"""Part B / PR 1 — SandboxManager abstraction + NoopSandboxManager.

The ``NoopSandboxManager`` is the host-side fallback: it runs commands
and file ops as host subprocesses / filesystem IO, exactly as the
ToolRegistry and verifier do today. It is the default whenever
``sandbox_enabled`` is false, so the loop is unchanged until the
DinD-backed manager is flipped on.
"""

from __future__ import annotations

import uuid

import pytest

from backend.src.core.sandbox import (
    NoopSandboxManager,
    SandboxError,
    SandboxResult,
)


def test_sandbox_result_is_frozen_dataclass() -> None:
    result = SandboxResult(exit_code=0, stdout="out", stderr="err", timed_out=False)
    assert result.exit_code == 0
    assert result.stdout == "out"
    assert result.stderr == "err"
    assert result.timed_out is False
    with pytest.raises((AttributeError, TypeError)):
        result.exit_code = 1  # type: ignore[misc]


async def test_acquire_returns_session_rooted_at_workspace(tmp_path) -> None:
    manager = NoopSandboxManager()
    project_id = uuid.uuid4()
    session = await manager.acquire(project_id, str(tmp_path))
    assert session.workspace_mount == str(tmp_path)


async def test_exec_runs_command_host_side(tmp_path) -> None:
    manager = NoopSandboxManager()
    session = await manager.acquire(uuid.uuid4(), str(tmp_path))
    result = await session.exec("echo hello", timeout_s=5)
    assert result.exit_code == 0
    assert "hello" in result.stdout
    assert result.timed_out is False


async def test_exec_shell_mode_evaluates_shell_syntax(tmp_path) -> None:
    manager = NoopSandboxManager()
    session = await manager.acquire(uuid.uuid4(), str(tmp_path))
    result = await session.exec("echo $((1 + 1))", timeout_s=5, shell=True)
    assert result.exit_code == 0
    assert "2" in result.stdout


async def test_exec_runs_in_workspace_cwd(tmp_path) -> None:
    (tmp_path / "marker.txt").write_text("x")
    manager = NoopSandboxManager()
    session = await manager.acquire(uuid.uuid4(), str(tmp_path))
    result = await session.exec("ls", timeout_s=5)
    assert "marker.txt" in result.stdout


async def test_exec_nonzero_exit_is_reported(tmp_path) -> None:
    manager = NoopSandboxManager()
    session = await manager.acquire(uuid.uuid4(), str(tmp_path))
    result = await session.exec("sh -c 'exit 3'", timeout_s=5)
    assert result.exit_code == 3


async def test_exec_unknown_command_is_127(tmp_path) -> None:
    manager = NoopSandboxManager()
    session = await manager.acquire(uuid.uuid4(), str(tmp_path))
    result = await session.exec("no_such_binary_xyz", timeout_s=5)
    assert result.exit_code == 127


async def test_exec_timeout_sets_timed_out(tmp_path) -> None:
    manager = NoopSandboxManager()
    session = await manager.acquire(uuid.uuid4(), str(tmp_path))
    result = await session.exec("sleep 5", timeout_s=0.3)
    assert result.timed_out is True


async def test_write_then_read_file_roundtrip(tmp_path) -> None:
    manager = NoopSandboxManager()
    session = await manager.acquire(uuid.uuid4(), str(tmp_path))
    await session.write_file("sub/app.py", b"print('hi')\n")
    data = await session.read_file("sub/app.py", max_bytes=1024)
    assert data == b"print('hi')\n"


async def test_read_file_respects_max_bytes(tmp_path) -> None:
    manager = NoopSandboxManager()
    session = await manager.acquire(uuid.uuid4(), str(tmp_path))
    await session.write_file("big.txt", b"abcdefghij")
    data = await session.read_file("big.txt", max_bytes=4)
    assert len(data) == 4
    assert data == b"abcd"


async def test_list_dir_returns_entries(tmp_path) -> None:
    (tmp_path / "a.py").write_text("")
    (tmp_path / "pkg").mkdir()
    manager = NoopSandboxManager()
    session = await manager.acquire(uuid.uuid4(), str(tmp_path))
    entries = await session.list_dir(".")
    assert "a.py" in entries
    assert any(e.startswith("pkg") for e in entries)


async def test_path_escape_is_refused(tmp_path) -> None:
    manager = NoopSandboxManager()
    session = await manager.acquire(uuid.uuid4(), str(tmp_path))
    with pytest.raises(SandboxError):
        await session.read_file("../escape.txt", max_bytes=64)
    with pytest.raises(SandboxError):
        await session.write_file("../escape.txt", b"x")


async def test_manager_health_release_reap_are_safe(tmp_path) -> None:
    manager = NoopSandboxManager()
    project_id = uuid.uuid4()
    await manager.acquire(project_id, str(tmp_path))
    assert await manager.health() is True
    # release + reap must never raise for the noop manager.
    await manager.release(project_id)
    await manager.reap_idle()
