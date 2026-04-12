"""Cancellation system — stop all agents in a project.

Provides a per-project cancellation token that executors check on each
iteration of their agentic loop. For worker-dispatched agents, a cancel
signal is published via Redis so the worker can kill its subprocess.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Redis channel for cancel signals to workers.
CANCEL_CHANNEL_PREFIX = "project:cancel:"


class CancellationToken:
    """In-process cancellation state for a project.

    Executors call ``check()`` at the top of each agentic loop iteration.
    If cancelled, the executor should stop and return a partial result.
    """

    _cancelled: dict[uuid.UUID, asyncio.Event] = {}

    @classmethod
    def _get_event(cls, project_id: uuid.UUID) -> asyncio.Event:
        if project_id not in cls._cancelled:
            cls._cancelled[project_id] = asyncio.Event()
        return cls._cancelled[project_id]

    @classmethod
    def cancel(cls, project_id: uuid.UUID) -> None:
        """Signal cancellation for all agents in a project."""
        cls._get_event(project_id).set()
        logger.info("project_cancelled", project_id=str(project_id))

    @classmethod
    def is_cancelled(cls, project_id: uuid.UUID) -> bool:
        """Check if a project has been cancelled."""
        event = cls._cancelled.get(project_id)
        return event is not None and event.is_set()

    @classmethod
    def reset(cls, project_id: uuid.UUID) -> None:
        """Clear cancellation state (e.g., when new work starts)."""
        if project_id in cls._cancelled:
            cls._cancelled[project_id].clear()

    @classmethod
    def cleanup(cls, project_id: uuid.UUID) -> None:
        """Remove cancellation state entirely."""
        cls._cancelled.pop(project_id, None)


class CancellationError(Exception):
    """Raised by executors when cancellation is detected."""

    def __init__(self, project_id: uuid.UUID) -> None:
        self.project_id = project_id
        super().__init__(f"Execution cancelled for project {project_id}")


async def cancel_project_agents(
    project_id: uuid.UUID,
    redis: Any | None = None,
) -> dict[str, Any]:
    """Cancel all running agents in a project.

    1. Set in-process cancellation token
    2. Publish cancel signal to Redis for workers
    3. Return status
    """
    # In-process cancellation
    CancellationToken.cancel(project_id)

    # Worker cancellation via Redis
    workers_notified = 0
    if redis is not None:
        channel = f"{CANCEL_CHANNEL_PREFIX}{project_id}"
        try:
            workers_notified = await redis.publish(channel, "cancel")
            logger.info(
                "cancel_signal_published",
                project_id=str(project_id),
                workers_notified=workers_notified,
            )
        except Exception as e:
            logger.warning("cancel_signal_failed", project_id=str(project_id), error=str(e))

    return {
        "project_id": str(project_id),
        "cancelled": True,
        "workers_notified": workers_notified,
    }
