"""Process-wide sandbox manager resolver.

The work loop is worker-driven (not a FastAPI request), so the manager
is a module singleton rather than a ``Depends``.

``sandbox_enabled`` false ⇒ **no manager** (``None``). Callers that get
``None`` pass ``sandbox_session=None`` downstream, so ToolRegistry and
``run_verification`` take their original host paths — byte-for-byte
the pre-Part-B behaviour. (A host-side ``NoopSandboxManager`` session
is NOT equivalent for verification: it would skip the verifier-venv
build that the host path relies on.)
"""

from __future__ import annotations

from backend.src.config import settings
from backend.src.core.sandbox.docker_manager import DockerSandboxManager
from backend.src.core.sandbox.protocol import SandboxManager

_manager: SandboxManager | None = None
_resolved = False


def build_sandbox_manager() -> SandboxManager | None:
    """Construct the manager from settings — ``DockerSandboxManager``
    when ``sandbox_enabled``, else ``None`` (host paths run unchanged)."""
    if settings.sandbox_enabled:
        return DockerSandboxManager(
            docker_host=settings.docker_host,
            sandbox_image=settings.sandbox_image,
            idle_reap_seconds=settings.sandbox_idle_reap_seconds,
            max_concurrent=settings.sandbox_max_concurrent,
        )
    return None


def get_sandbox_manager() -> SandboxManager | None:
    """The process-wide sandbox manager (lazily built, cached). ``None``
    when ``sandbox_enabled`` is false."""
    global _manager, _resolved
    if not _resolved:
        _manager = build_sandbox_manager()
        _resolved = True
    return _manager


def reset_sandbox_manager() -> None:
    """Test hook — drop the cached singleton so the next ``get`` rebuilds."""
    global _manager, _resolved
    _manager = None
    _resolved = False
