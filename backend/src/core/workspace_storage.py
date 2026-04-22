"""Pluggable workspace storage providers.

Storage decides where the working copy of an imported project lives:

- LocalWorkspaceStorage: ``./data/workspaces/{project_id}/`` on the
  host running BSNexus. Default for self-hosted setups.
- GitWorkspaceStorage: initialise a fresh git repo, push to a remote
  the user provides. Used when teams want their workspace versioned
  and reachable from outside the BSNexus host.

Future providers (S3, GitHub-app-managed) plug in behind the same
``WorkspaceStorage`` protocol.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class WorkspaceLocation:
    """Where to find a project workspace once it has been provisioned."""

    project_id: uuid.UUID
    local_path: Path
    remote_url: str | None = None


class WorkspaceStorage(Protocol):
    async def provision(self, project_id: uuid.UUID, source_dir: Path) -> WorkspaceLocation: ...

    async def teardown(self, project_id: uuid.UUID) -> None: ...


# ── Concrete providers ──────────────────────────────────────────────


class LocalWorkspaceStorage:
    """Store the workspace under a configurable root on the local filesystem."""

    def __init__(self, root: Path) -> None:
        self.root = root

    async def provision(self, project_id: uuid.UUID, source_dir: Path) -> WorkspaceLocation:
        target = self.root / str(project_id)
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copytree, source_dir, target)
        return WorkspaceLocation(project_id=project_id, local_path=target)

    async def teardown(self, project_id: uuid.UUID) -> None:
        target = self.root / str(project_id)
        if target.exists():
            await asyncio.to_thread(shutil.rmtree, target, True)


class GitWorkspaceStorage:
    """Materialise the workspace locally and also push to a git remote.

    The remote must already exist and the BSNexus process must have
    credentials in its git config (or via SSH agent). The provider does
    not generate keys or create repositories on its own.
    """

    def __init__(self, root: Path, remote_url: str, branch: str = "main") -> None:
        self.root = root
        self.remote_url = remote_url
        self.branch = branch

    async def provision(self, project_id: uuid.UUID, source_dir: Path) -> WorkspaceLocation:
        target = self.root / str(project_id)
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copytree, source_dir, target)

        await _run("git", "-C", str(target), "init", "-b", self.branch)
        await _run("git", "-C", str(target), "add", ".")
        await _run(
            "git",
            "-C",
            str(target),
            "-c",
            "user.email=bsnexus@example.com",
            "-c",
            "user.name=BSNexus",
            "commit",
            "-m",
            "import: initial commit",
        )
        await _run("git", "-C", str(target), "remote", "add", "origin", self.remote_url)
        await _run("git", "-C", str(target), "push", "-u", "origin", self.branch)
        return WorkspaceLocation(project_id=project_id, local_path=target, remote_url=self.remote_url)

    async def teardown(self, project_id: uuid.UUID) -> None:
        target = self.root / str(project_id)
        if target.exists():
            await asyncio.to_thread(shutil.rmtree, target, True)


# ── Helpers ─────────────────────────────────────────────────────────


async def _run(*cmd: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed ({' '.join(cmd)}): {stderr.decode().strip()}")
