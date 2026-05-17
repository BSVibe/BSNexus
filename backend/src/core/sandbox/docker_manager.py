"""DinD-backed sandbox manager — the single ``docker`` shell-out site.

``DockerSandboxManager`` runs every project's work phase + verification
inside a per-project container managed in the ``bsnexus-sandbox-dind``
sidecar. It is the only module that invokes the ``docker`` CLI; all
docker interaction funnels through ``_docker``, which is the unit-test
mock boundary.
"""

from __future__ import annotations

import asyncio
import os
import posixpath
import shlex
import time
import uuid
from dataclasses import dataclass

import structlog

from backend.src.core.sandbox.errors import SandboxError, SandboxUnavailable
from backend.src.core.sandbox.protocol import SandboxResult

logger = structlog.get_logger(__name__)

_CONTAINER_PREFIX = "bsnexus-sbx-"
_WORK_MOUNT = "/work"
_DIND_STARTUP_TIMEOUT_S = 30.0
_DOCKER_OP_TIMEOUT_S = 60.0
_SANDBOX_MEMORY = "4g"


def _container_name(project_id: uuid.UUID) -> str:
    return f"{_CONTAINER_PREFIX}{project_id}"


def _safe_rel(rel_path: str) -> str:
    """Normalize a workspace-relative path, refusing escapes. The
    ``/work`` mount does not by itself stop ``/work/../etc`` — guard it."""
    norm = posixpath.normpath(rel_path or ".")
    if norm.startswith("..") or norm.startswith("/"):
        raise SandboxError(f"path {rel_path!r} escapes the workspace")
    return norm


@dataclass
class _Entry:
    name: str
    last_used: float


class DockerSandboxSession:
    """A handle to one project's running sandbox container. Commands
    and file ops run via ``docker exec``."""

    def __init__(self, *, container: str, docker: "DockerSandboxManager") -> None:
        self._container = container
        self._mgr = docker

    @property
    def workspace_mount(self) -> str:
        return _WORK_MOUNT

    async def exec(self, command: str, *, timeout_s: float, shell: bool = False) -> SandboxResult:
        if shell:
            inner = ["sh", "-c", command]
        else:
            try:
                parts = shlex.split(command)
            except ValueError as exc:
                raise SandboxError(f"bad shell syntax: {exc}") from exc
            if not parts:
                raise SandboxError("empty command")
            inner = parts
        code, out, err = await self._mgr._docker(
            ["exec", "-w", _WORK_MOUNT, self._container, *inner],
            timeout_s=timeout_s,
        )
        return SandboxResult(
            exit_code=code,
            stdout=out.decode("utf-8", errors="replace"),
            stderr=err.decode("utf-8", errors="replace"),
            timed_out=code is None,
        )

    async def read_file(self, rel_path: str, max_bytes: int) -> bytes:
        norm = _safe_rel(rel_path)
        code, out, err = await self._mgr._docker(
            ["exec", self._container, "cat", f"{_WORK_MOUNT}/{norm}"],
            timeout_s=_DOCKER_OP_TIMEOUT_S,
        )
        if code != 0:
            raise SandboxError(f"read_file: {err.decode('utf-8', errors='replace').strip() or 'failed'}")
        return out[:max_bytes]

    async def write_file(self, rel_path: str, content: bytes) -> None:
        norm = _safe_rel(rel_path)
        target = f"{_WORK_MOUNT}/{norm}"
        # mkdir parents, then write stdin to the file — one exec.
        script = f'mkdir -p "$(dirname {shlex.quote(target)})" && cat > {shlex.quote(target)}'
        code, _out, err = await self._mgr._docker(
            ["exec", "-i", self._container, "sh", "-c", script],
            timeout_s=_DOCKER_OP_TIMEOUT_S,
            stdin=content,
        )
        if code != 0:
            raise SandboxError(f"write_file: {err.decode('utf-8', errors='replace').strip() or 'failed'}")

    async def list_dir(self, rel_path: str) -> list[str]:
        norm = _safe_rel(rel_path)
        code, out, err = await self._mgr._docker(
            ["exec", self._container, "sh", "-c", f"ls -A -p {shlex.quote(f'{_WORK_MOUNT}/{norm}')}"],
            timeout_s=_DOCKER_OP_TIMEOUT_S,
        )
        if code != 0:
            raise SandboxError(f"list_dir: {err.decode('utf-8', errors='replace').strip() or 'failed'}")
        return sorted(line for line in out.decode("utf-8", errors="replace").splitlines() if line)


class DockerSandboxManager:
    """Per-project sandbox lifecycle over a DinD daemon.

    One container per project (``bsnexus-sbx-<project_id>``), created
    lazily, reused across RunAttempts, reaped on idle. ``sandbox_max_
    concurrent`` bounds the number of live containers — a permit is
    held for a container's lifetime, not just its creation.
    """

    def __init__(
        self,
        *,
        docker_host: str,
        sandbox_image: str,
        idle_reap_seconds: int,
        max_concurrent: int,
    ) -> None:
        self._docker_host = docker_host
        self._image = sandbox_image
        self._idle_reap_seconds = idle_reap_seconds
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._locks: dict[uuid.UUID, asyncio.Lock] = {}
        self._containers: dict[uuid.UUID, _Entry] = {}
        self._held: set[uuid.UUID] = set()
        self._registry_lock = asyncio.Lock()

    async def _docker(
        self,
        argv: list[str],
        *,
        timeout_s: float,
        stdin: bytes | None = None,
    ) -> tuple[int | None, bytes, bytes]:
        """The single docker-CLI boundary. Returns ``(exit_code,
        stdout, stderr)``; ``exit_code`` is ``None`` on timeout. Unit
        tests patch this method."""
        env = dict(os.environ)
        if self._docker_host:
            env["DOCKER_HOST"] = self._docker_host
        proc = await asyncio.create_subprocess_exec(
            "docker",
            *argv,
            stdin=asyncio.subprocess.PIPE if stdin is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(input=stdin), timeout=timeout_s)
        except TimeoutError:
            proc.kill()
            await proc.communicate()
            return None, b"", f"docker timed out after {timeout_s}s".encode()
        return proc.returncode, out, err

    async def health(self) -> bool:
        code, _out, _err = await self._docker(["version", "--format", "{{.Server.Version}}"], timeout_s=10.0)
        return code == 0

    async def _await_dind(self) -> None:
        """Poll the DinD endpoint until reachable — it has a multi-second
        start-up (TLS deprecation warning) before TCP 2375 is up."""
        deadline = time.monotonic() + _DIND_STARTUP_TIMEOUT_S
        while True:
            if await self.health():
                return
            if time.monotonic() >= deadline:
                raise SandboxUnavailable(
                    f"sandbox DinD unreachable at {self._docker_host or '(default)'} after {_DIND_STARTUP_TIMEOUT_S}s"
                )
            await asyncio.sleep(1.0)

    async def _is_running(self, name: str) -> bool:
        code, out, _err = await self._docker(["inspect", "-f", "{{.State.Running}}", name], timeout_s=10.0)
        return code == 0 and out.decode().strip() == "true"

    async def acquire(self, project_id: uuid.UUID, workspace_path: str) -> DockerSandboxSession:
        async with self._registry_lock:
            lock = self._locks.setdefault(project_id, asyncio.Lock())
        async with lock:
            entry = self._containers.get(project_id)
            if entry is not None and await self._is_running(entry.name):
                entry.last_used = time.monotonic()
                return DockerSandboxSession(container=entry.name, docker=self)
            # Drop a stale registry entry / dead container before recreating.
            if entry is not None:
                await self._teardown(project_id)
            return await self._create(project_id, workspace_path)

    async def _create(self, project_id: uuid.UUID, workspace_path: str) -> DockerSandboxSession:
        await self._await_dind()
        name = _container_name(project_id)
        # Clear any orphan with this name (e.g. from a prior process).
        await self._docker(["rm", "-f", name], timeout_s=_DOCKER_OP_TIMEOUT_S)
        await self._semaphore.acquire()
        try:
            code, _out, err = await self._docker(
                [
                    "run",
                    "-d",
                    "--name",
                    name,
                    "--memory",
                    _SANDBOX_MEMORY,
                    "--memory-swap",
                    _SANDBOX_MEMORY,
                    "-v",
                    f"{workspace_path}:{_WORK_MOUNT}",
                    "-w",
                    _WORK_MOUNT,
                    self._image,
                    "sleep",
                    "infinity",
                ],
                timeout_s=_DOCKER_OP_TIMEOUT_S,
            )
        except BaseException:
            self._semaphore.release()
            raise
        if code != 0:
            self._semaphore.release()
            raise SandboxError(f"sandbox create failed: {err.decode('utf-8', errors='replace').strip()}")
        self._containers[project_id] = _Entry(name=name, last_used=time.monotonic())
        self._held.add(project_id)
        logger.info("sandbox_created", project_id=str(project_id), container=name)
        return DockerSandboxSession(container=name, docker=self)

    async def _teardown(self, project_id: uuid.UUID) -> None:
        """Remove a project's container and release its concurrency
        permit. Idempotent."""
        entry = self._containers.pop(project_id, None)
        if entry is not None:
            await self._docker(["rm", "-f", entry.name], timeout_s=_DOCKER_OP_TIMEOUT_S)
            logger.info("sandbox_removed", project_id=str(project_id), container=entry.name)
        if project_id in self._held:
            self._held.discard(project_id)
            self._semaphore.release()

    async def release(self, project_id: uuid.UUID) -> None:
        async with self._registry_lock:
            lock = self._locks.setdefault(project_id, asyncio.Lock())
        async with lock:
            await self._teardown(project_id)

    async def reap_idle(self) -> None:
        now = time.monotonic()
        stale = [
            pid for pid, entry in list(self._containers.items()) if now - entry.last_used >= self._idle_reap_seconds
        ]
        for pid in stale:
            await self.release(pid)
