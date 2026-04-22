"""In-process cancellation token for the agentic loop.

Executors check ``CancellationToken.is_cancelled(project_id)`` at each
iteration of the LLM → tool_call → execute loop. The stop-all endpoint
sets the flag via ``CancellationToken.cancel()``.

For task-level cancellation, ``agent_control.py`` also transitions running
tasks to blocked via the state machine (which publishes SSE events).
"""

from __future__ import annotations

import asyncio
import uuid

import structlog

logger = structlog.get_logger(__name__)


class CancellationToken:
    """In-process cancellation state for a project.

    Executors call ``is_cancelled()`` at the top of each agentic loop
    iteration. If cancelled, the executor returns a partial result.
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
