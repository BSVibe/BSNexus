"""Process-wide sandbox manager resolver.

The work loop is worker-driven (not a FastAPI request), so the manager
is a module singleton rather than a ``Depends``. ``sandbox_enabled``
decides Docker vs the host-side Noop.
"""

from __future__ import annotations

from backend.src.config import settings
from backend.src.core.sandbox.docker_manager import DockerSandboxManager
from backend.src.core.sandbox.noop_manager import NoopSandboxManager
from backend.src.core.sandbox.protocol import SandboxManager

_manager: SandboxManager | None = None


def build_sandbox_manager() -> SandboxManager:
    """Construct a manager from settings — ``DockerSandboxManager``
    when ``sandbox_enabled``, else ``NoopSandboxManager`` (host-side)."""
    if settings.sandbox_enabled:
        return DockerSandboxManager(
            docker_host=settings.docker_host,
            sandbox_image=settings.sandbox_image,
            idle_reap_seconds=settings.sandbox_idle_reap_seconds,
            max_concurrent=settings.sandbox_max_concurrent,
        )
    return NoopSandboxManager()


def get_sandbox_manager() -> SandboxManager:
    """The process-wide sandbox manager (lazily built, cached)."""
    global _manager
    if _manager is None:
        _manager = build_sandbox_manager()
    return _manager


def reset_sandbox_manager() -> None:
    """Test hook — drop the cached singleton so the next ``get`` rebuilds."""
    global _manager
    _manager = None
