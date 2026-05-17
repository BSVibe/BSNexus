"""Background sandbox idle reaper.

``DockerSandboxManager.reap_idle()`` tears down per-project sandbox
containers idle past the threshold — but nothing calls it on its own.
This loop, started from the FastAPI lifespan, drives it periodically
so idle sandboxes don't accumulate and pin host memory.
"""

from __future__ import annotations

import asyncio

import structlog

from backend.src.core.sandbox.protocol import SandboxManager

logger = structlog.get_logger(__name__)

# How often to sweep for idle sandboxes. The idle *threshold* is
# ``sandbox_idle_reap_seconds`` (checked inside ``reap_idle``); this is
# just the poll cadence.
REAP_INTERVAL_S: float = 300.0


async def sandbox_reaper_loop(manager: SandboxManager, *, interval_s: float = REAP_INTERVAL_S) -> None:
    """Periodically call ``manager.reap_idle()``. Runs until cancelled.
    A failing reap (DinD hiccup) is logged and the loop continues —
    one bad sweep must not kill the reaper."""
    while True:
        try:
            await manager.reap_idle()
        except Exception:  # noqa: BLE001 — a reap failure must not stop the loop
            logger.exception("sandbox_reaper_failed")
        await asyncio.sleep(interval_s)
