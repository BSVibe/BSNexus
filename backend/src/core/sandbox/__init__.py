"""Work sandbox (Part B) — per-project disposable execution containers.

The work LLM's ``shell_exec`` / file tools and the verifier's declared
``command`` checks run inside a per-project sandbox container managed
in a DinD sidecar, not as host subprocesses in the BSNexus container.

``SandboxManager`` / ``SandboxSession`` are the seam; ``docker`` is
shelled out from exactly one implementation. ``NoopSandboxManager`` is
the host-side fallback used while ``sandbox_enabled`` is false.
"""

from __future__ import annotations

from backend.src.core.sandbox.errors import SandboxError, SandboxUnavailable
from backend.src.core.sandbox.noop_manager import NoopSandboxManager, NoopSandboxSession
from backend.src.core.sandbox.protocol import SandboxManager, SandboxResult, SandboxSession

__all__ = [
    "NoopSandboxManager",
    "NoopSandboxSession",
    "SandboxError",
    "SandboxManager",
    "SandboxResult",
    "SandboxSession",
    "SandboxUnavailable",
]
