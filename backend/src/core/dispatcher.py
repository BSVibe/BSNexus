"""Task dispatcher — routes tasks to local execution or remote workers."""

from __future__ import annotations

import uuid
from enum import Enum

import structlog

from backend.src.core.executor.registry import ExecutorRegistry

logger = structlog.get_logger(__name__)


class DispatchTarget(str, Enum):
    local = "local"
    remote = "remote"


class TaskDispatcher:
    """Decides whether to execute a task locally or dispatch to a remote worker.

    Decision criteria:
    1. If executor requires_local=True (e.g., ClaudeCodeExecutor) → local
    2. If matching remote worker is online → remote
    3. Fallback → local
    """

    def resolve_target(self, executor_type: str) -> DispatchTarget:
        """Determine dispatch target for an executor type."""
        registry = ExecutorRegistry()
        info = registry.get_info(executor_type)

        if info is not None and info.requires_local:
            return DispatchTarget.local

        # TODO: Check for available remote workers matching executor_type
        # For now, always dispatch locally
        return DispatchTarget.local

    async def dispatch_to_remote(self, task_id: uuid.UUID, executor_type: str) -> None:
        """Publish task to Redis stream for remote worker pickup.

        TODO: Implement when remote worker protocol is wired.
        """
        logger.info(
            "remote_dispatch_placeholder",
            task_id=str(task_id),
            executor_type=executor_type,
        )
