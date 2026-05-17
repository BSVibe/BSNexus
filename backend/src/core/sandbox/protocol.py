"""Sandbox protocol — the seam between the work loop / verifier and
the execution backend.

``SandboxManager`` owns per-project sandbox lifecycle; ``SandboxSession``
is a handle to one project's running sandbox. Everything that needs to
run a command or touch a file (``ToolRegistry``, ``verification.py``)
depends on these Protocols — never on ``docker`` directly. The docker
shell-out lives in exactly one implementation (``DockerSandboxManager``);
``NoopSandboxManager`` is the host-side fallback.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class SandboxResult:
    """Outcome of one command run inside a sandbox session.

    ``exit_code`` is ``None`` when the command was killed before it
    could exit (timeout). ``timed_out`` disambiguates that case.
    """

    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool


@runtime_checkable
class SandboxSession(Protocol):
    """A handle to one project's running sandbox. All work-phase tool
    execution and verification commands run through this handle."""

    @property
    def workspace_mount(self) -> str:
        """The workspace root path as seen by commands run via ``exec``
        (the host path for the noop backend, ``/work`` inside a
        container for the docker backend)."""
        ...

    async def exec(self, command: str, *, timeout_s: float, shell: bool = False) -> SandboxResult:
        """Run ``command`` with cwd = the workspace root. ``shell=True``
        evaluates it through ``sh -c``; ``shell=False`` splits and execs
        it directly (no shell metacharacter evaluation)."""
        ...

    async def read_file(self, rel_path: str, max_bytes: int) -> bytes:
        """Read a workspace-relative file, capped at ``max_bytes``."""
        ...

    async def write_file(self, rel_path: str, content: bytes) -> None:
        """Create/overwrite a workspace-relative file, making parents."""
        ...

    async def list_dir(self, rel_path: str) -> list[str]:
        """List a workspace-relative directory; dir entries end in ``/``."""
        ...


@runtime_checkable
class SandboxManager(Protocol):
    """Per-project sandbox lifecycle. One sandbox per project, created
    lazily on first work dispatch, reused across RunAttempts and
    WorkSteps, reaped on idle."""

    async def acquire(self, project_id: uuid.UUID, workspace_path: str) -> SandboxSession:
        """Return the project's sandbox session, creating it if needed.
        Idempotent — concurrent callers for one project await a single
        creation."""
        ...

    async def release(self, project_id: uuid.UUID) -> None:
        """Tear down the project's sandbox now, if any."""
        ...

    async def reap_idle(self) -> None:
        """Tear down sandboxes idle past the configured threshold."""
        ...

    async def health(self) -> bool:
        """True when the sandbox backend is reachable."""
        ...
