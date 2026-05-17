"""Part B / PR 2 — DockerSandboxManager live-DinD integration test.

Skipped unless ``BSNEXUS_SANDBOX_DOCKER_HOST`` points at a reachable
DinD daemon with the ``bsnexus-sandbox`` image loaded. Run inside a
devcontainer that has the sandbox-dind sidecar up:

    BSNEXUS_SANDBOX_DOCKER_HOST=tcp://sandbox-dind:2375 \
      uv run --project . pytest tests/test_sandbox_docker_integration.py
"""

from __future__ import annotations

import os
import uuid

import pytest

from backend.src.core.sandbox import DockerSandboxManager

_DOCKER_HOST = os.getenv("BSNEXUS_SANDBOX_DOCKER_HOST", "")
_SANDBOX_IMAGE = os.getenv("BSNEXUS_SANDBOX_IMAGE", "bsnexus-sandbox:latest")

pytestmark = pytest.mark.skipif(
    not _DOCKER_HOST,
    reason="set BSNEXUS_SANDBOX_DOCKER_HOST to run the live-DinD sandbox test",
)


def _manager() -> DockerSandboxManager:
    return DockerSandboxManager(
        docker_host=_DOCKER_HOST,
        sandbox_image=_SANDBOX_IMAGE,
        idle_reap_seconds=1800,
        max_concurrent=2,
    )


async def test_real_sandbox_lifecycle(tmp_path) -> None:
    mgr = _manager()
    assert await mgr.health() is True
    project_id = uuid.uuid4()
    try:
        session = await mgr.acquire(project_id, str(tmp_path))

        # exec
        result = await session.exec("echo sandbox-ok", timeout_s=30)
        assert result.exit_code == 0
        assert "sandbox-ok" in result.stdout

        # the toolchain is present (this is the whole point of Part B)
        pytest_check = await session.exec("python -m pytest --version", timeout_s=60)
        assert pytest_check.exit_code == 0

        # file round-trip
        await session.write_file("pkg/mod.py", b"VALUE = 42\n")
        data = await session.read_file("pkg/mod.py", max_bytes=1024)
        assert data == b"VALUE = 42\n"
        entries = await session.list_dir(".")
        assert any(e.startswith("pkg") for e in entries)

        # reuse returns the same container
        again = await mgr.acquire(project_id, str(tmp_path))
        assert again._container == session._container
    finally:
        await mgr.release(project_id)
