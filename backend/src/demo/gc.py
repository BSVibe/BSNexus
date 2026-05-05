"""BSNexus demo tenant GC — hourly cron entrypoint.

Wraps :func:`bsvibe_demo.demo_gc_sqlalchemy` with BSNexus's existing
SQLAlchemy ``async_session`` factory. Cascade-deletes via the existing
``ON DELETE CASCADE`` chain on ``Project.tenant_id`` and friends.
"""

from __future__ import annotations

import asyncio
import os

import structlog
from bsvibe_demo import demo_gc_sqlalchemy

from backend.src.storage.database import async_session

logger = structlog.get_logger(__name__)


async def _run() -> None:
    ttl = int(os.environ.get("DEMO_TTL_SECONDS", "7200"))
    deleted = await demo_gc_sqlalchemy(async_session, ttl_seconds=ttl)
    print(f"demo_gc: deleted {deleted} expired tenant(s)")


def main() -> None:
    """CLI entrypoint: ``python -m backend.src.demo.gc``."""
    asyncio.run(_run())


if __name__ == "__main__":
    main()
