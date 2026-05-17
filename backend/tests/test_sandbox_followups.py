"""Part B follow-ups — sandbox idle reaper + provision_workspace
stale-path hardening.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

from backend.src.core import orchestration
from backend.src.core.sandbox.reaper import sandbox_reaper_loop
from backend.src.models.project import WorkspaceType


# ───────────────────────── sandbox idle reaper ────────────────────────────


class _CountingManager:
    def __init__(self) -> None:
        self.reap_calls = 0

    async def reap_idle(self) -> None:
        self.reap_calls += 1


async def test_sandbox_reaper_loop_calls_reap_idle() -> None:
    mgr = _CountingManager()
    task = asyncio.create_task(sandbox_reaper_loop(mgr, interval_s=0.01))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert mgr.reap_calls >= 1


async def test_sandbox_reaper_loop_survives_a_failing_reap() -> None:
    class _Flaky:
        def __init__(self) -> None:
            self.calls = 0

        async def reap_idle(self) -> None:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("dind hiccup")

    mgr = _Flaky()
    task = asyncio.create_task(sandbox_reaper_loop(mgr, interval_s=0.01))
    await asyncio.sleep(0.06)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    # the first reap raised; the loop kept going and reaped again.
    assert mgr.calls >= 2


# ─────────────────── provision_workspace stale-path hardening ──────────────


def test_provision_workspace_reprovisions_stale_server_managed_dir(tmp_path, monkeypatch) -> None:
    """A server_managed project whose workspace_dir points OUTSIDE the
    current workspace_root is stale (stamped before a volume-layout
    migration) — re-provision under the managed root and re-stamp."""
    monkeypatch.setattr(orchestration.app_settings, "workspace_root", str(tmp_path))
    pid = uuid.uuid4()
    project = SimpleNamespace(
        id=pid,
        workspace_dir=f"/app/backend/data/workspaces/{pid}",
        workspace_type=WorkspaceType.server_managed,
    )
    path = orchestration.provision_workspace(project)
    assert str(path).startswith(str(tmp_path.resolve()))
    assert project.workspace_dir == str(path)  # re-stamped to the corrected path


def test_provision_workspace_keeps_in_root_server_managed_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(orchestration.app_settings, "workspace_root", str(tmp_path))
    pid = uuid.uuid4()
    in_root = str(tmp_path / str(pid))
    project = SimpleNamespace(id=pid, workspace_dir=in_root, workspace_type=WorkspaceType.server_managed)
    path = orchestration.provision_workspace(project)
    assert str(path) == in_root  # already under workspace_root → untouched


def test_provision_workspace_keeps_local_import_dir(tmp_path, monkeypatch) -> None:
    """local_import / github_connected projects intentionally carry a
    workspace_dir OUTSIDE workspace_root — the hardening must not touch
    them."""
    monkeypatch.setattr(orchestration.app_settings, "workspace_root", str(tmp_path / "managed"))
    outside = tmp_path / "imported_repo"
    outside.mkdir()
    project = SimpleNamespace(
        id=uuid.uuid4(),
        workspace_dir=str(outside),
        workspace_type=WorkspaceType.local_import,
    )
    path = orchestration.provision_workspace(project)
    assert path == outside
    assert project.workspace_dir == str(outside)  # untouched
