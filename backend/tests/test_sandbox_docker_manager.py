"""Part B / PR 2 — DockerSandboxManager unit tests.

Every test mocks ``_docker`` (the single docker-CLI boundary) — no real
docker daemon. A live-DinD integration test lives in
``test_sandbox_docker_integration.py``.
"""

from __future__ import annotations

import uuid

import pytest

from backend.src.core import sandbox as sandbox_pkg
from backend.src.core.sandbox import DockerSandboxManager, SandboxError, SandboxUnavailable


class FakeDocker:
    """Scriptable stand-in for ``DockerSandboxManager._docker``.

    Dispatches on the docker subcommand (``argv[0]``); records every
    call so tests can assert the exact argv."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.stdins: list[bytes | None] = []
        self.version_ok = True
        self.running = "false"
        self.run_code = 0
        self.exec_result: tuple[int | None, bytes, bytes] = (0, b"", b"")

    async def __call__(self, argv, *, timeout_s, stdin=None):
        self.calls.append(list(argv))
        self.stdins.append(stdin)
        cmd = argv[0]
        if cmd == "version":
            return (0 if self.version_ok else 1), b"27.0\n", b""
        if cmd == "inspect":
            return 0, (self.running + "\n").encode(), b""
        if cmd == "rm":
            return 0, b"", b""
        if cmd == "run":
            return self.run_code, b"cid\n", (b"" if self.run_code == 0 else b"run boom")
        if cmd == "exec":
            return self.exec_result
        return 0, b"", b""

    def argv_with(self, *needles: str) -> list[str] | None:
        for argv in self.calls:
            if all(any(n in part for part in argv) for n in needles):
                return argv
        return None


def _manager(max_concurrent: int = 2) -> DockerSandboxManager:
    return DockerSandboxManager(
        docker_host="tcp://dind:2375",
        sandbox_image="bsnexus-sandbox:latest",
        idle_reap_seconds=1800,
        max_concurrent=max_concurrent,
    )


async def test_health_reflects_docker_exit_code() -> None:
    mgr = _manager()
    fake = FakeDocker()
    mgr._docker = fake  # type: ignore[method-assign]
    assert await mgr.health() is True
    fake.version_ok = False
    assert await mgr.health() is False


async def test_acquire_creates_container_with_workspace_mount() -> None:
    mgr = _manager()
    fake = FakeDocker()
    mgr._docker = fake  # type: ignore[method-assign]
    project_id = uuid.uuid4()

    session = await mgr.acquire(project_id, "/workspaces/proj")

    assert session.workspace_mount == "/work"
    run_argv = fake.argv_with("run", f"bsnexus-sbx-{project_id}")
    assert run_argv is not None
    assert "/workspaces/proj:/work" in run_argv
    assert "--memory" in run_argv
    assert "sleep" in run_argv and "infinity" in run_argv


async def test_acquire_reuses_running_container() -> None:
    mgr = _manager()
    fake = FakeDocker()
    mgr._docker = fake  # type: ignore[method-assign]
    project_id = uuid.uuid4()

    await mgr.acquire(project_id, "/workspaces/proj")
    fake.running = "true"
    fake.calls.clear()

    await mgr.acquire(project_id, "/workspaces/proj")
    # Reuse path inspects, never runs a second container.
    assert fake.argv_with("run") is None
    assert fake.argv_with("inspect") is not None


async def test_acquire_failure_releases_concurrency_permit() -> None:
    mgr = _manager(max_concurrent=1)
    fake = FakeDocker()
    fake.run_code = 1
    mgr._docker = fake  # type: ignore[method-assign]

    with pytest.raises(SandboxError, match="run boom"):
        await mgr.acquire(uuid.uuid4(), "/workspaces/a")
    # The permit must be back — a failed create cannot leak a slot.
    assert mgr._semaphore.locked() is False


async def test_dind_unreachable_raises_sandbox_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(sandbox_pkg.docker_manager, "_DIND_STARTUP_TIMEOUT_S", 0.01)
    mgr = _manager()
    fake = FakeDocker()
    fake.version_ok = False
    mgr._docker = fake  # type: ignore[method-assign]

    with pytest.raises(SandboxUnavailable):
        await mgr.acquire(uuid.uuid4(), "/workspaces/a")


async def test_session_exec_routes_through_docker_exec() -> None:
    mgr = _manager()
    fake = FakeDocker()
    mgr._docker = fake  # type: ignore[method-assign]
    session = await mgr.acquire(uuid.uuid4(), "/workspaces/a")

    fake.exec_result = (0, b"hello\n", b"")
    fake.calls.clear()
    result = await session.exec("echo hello", timeout_s=5)
    assert result.exit_code == 0
    assert "hello" in result.stdout
    exec_argv = fake.calls[0]
    assert exec_argv[:4] == ["exec", "-w", "/work", session._container]
    assert exec_argv[-2:] == ["echo", "hello"]


async def test_session_exec_shell_mode_uses_sh_c() -> None:
    mgr = _manager()
    fake = FakeDocker()
    mgr._docker = fake  # type: ignore[method-assign]
    session = await mgr.acquire(uuid.uuid4(), "/workspaces/a")

    fake.calls.clear()
    await session.exec("echo $((1 + 1))", timeout_s=5, shell=True)
    assert fake.calls[0][-3:] == ["sh", "-c", "echo $((1 + 1))"]


async def test_session_read_file_caps_bytes() -> None:
    mgr = _manager()
    fake = FakeDocker()
    mgr._docker = fake  # type: ignore[method-assign]
    session = await mgr.acquire(uuid.uuid4(), "/workspaces/a")

    fake.exec_result = (0, b"abcdefghij", b"")
    data = await session.read_file("app.py", max_bytes=4)
    assert data == b"abcd"


async def test_session_write_file_streams_content_via_stdin() -> None:
    mgr = _manager()
    fake = FakeDocker()
    mgr._docker = fake  # type: ignore[method-assign]
    session = await mgr.acquire(uuid.uuid4(), "/workspaces/a")

    fake.calls.clear()
    fake.stdins.clear()
    await session.write_file("sub/app.py", b"print('hi')\n")
    assert fake.stdins[0] == b"print('hi')\n"
    assert "sh" in fake.calls[0] and "-c" in fake.calls[0]


async def test_session_file_ops_refuse_path_escape() -> None:
    mgr = _manager()
    fake = FakeDocker()
    mgr._docker = fake  # type: ignore[method-assign]
    session = await mgr.acquire(uuid.uuid4(), "/workspaces/a")

    with pytest.raises(SandboxError):
        await session.read_file("../etc/passwd", max_bytes=10)
    with pytest.raises(SandboxError):
        await session.write_file("/abs/path", b"x")


async def test_release_removes_container_and_frees_permit() -> None:
    mgr = _manager(max_concurrent=1)
    fake = FakeDocker()
    mgr._docker = fake  # type: ignore[method-assign]
    project_id = uuid.uuid4()

    await mgr.acquire(project_id, "/workspaces/a")
    assert mgr._semaphore.locked() is True
    fake.calls.clear()

    await mgr.release(project_id)
    assert fake.argv_with("rm") is not None
    assert mgr._semaphore.locked() is False
    assert project_id not in mgr._containers


async def test_reap_idle_removes_stale_sandboxes() -> None:
    mgr = _manager()
    mgr._idle_reap_seconds = 0  # everything is immediately stale
    fake = FakeDocker()
    mgr._docker = fake  # type: ignore[method-assign]
    project_id = uuid.uuid4()

    await mgr.acquire(project_id, "/workspaces/a")
    await mgr.reap_idle()
    assert project_id not in mgr._containers
