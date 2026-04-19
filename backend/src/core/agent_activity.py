"""Agent status resolution — derived from task state + executor availability.

Agent status is NOT stored in Redis transient keys. Instead, it is
computed from the current task state (running/blocked) and executor
availability (LLM API config / worker pool). This eliminates timing
races between SSE events and cache invalidation.

Status colours
--------------

* ``red``    — has a *blocked* task assigned (needs human attention)
* ``green``  — has a *running* task assigned (actively working)
* ``yellow`` — online and idle (executor available, no active task)
* ``gray``   — offline / unreachable
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from backend.src.models import Agent, Task


# ── Worker availability ─────────────────────────────────────────────


async def has_online_worker(db: AsyncSession, tenant_id: uuid.UUID) -> bool:
    """True if any active worker in the tenant is currently online."""
    from backend.src.models import Worker

    result = await db.execute(
        select(Worker.id).where(
            Worker.tenant_id == tenant_id,
            Worker.is_active.is_(True),
            Worker.status == "online",
        ).limit(1)
    )
    return result.scalar_one_or_none() is not None


# ── Status dot resolver ─────────────────────────────────────────────


def _is_llm_api_executor(executor_type: str) -> bool:
    """Check if the executor type is an LLM API (always-on, no heartbeat)."""
    return executor_type in ("generic_llm", "bsgateway", "codex")


def resolve_agent_status_dot(
    agent: "Agent",
    *,
    current_task: "Task | None" = None,
    online_worker_available: bool = False,
    is_processing: bool = False,
) -> str:
    """Return the canonical status dot colour for an agent.

    Precedence (high → low):

    1. ``red``    — assigned a blocked task
    2. ``green``  — assigned a running task OR actively processing (LLM call in progress)
    3. ``yellow`` — online + idle (executor available)
    4. ``gray``   — offline
    """
    from backend.src.models import TaskStatus

    if current_task is not None:
        if current_task.status == TaskStatus.blocked:
            return "red"
        if current_task.status == TaskStatus.running:
            return "green"

    # Agent is processing (active/passive mode, LLM call in progress)
    if is_processing:
        return "green"

    # LLM API executors are always-on when configured.
    if _is_llm_api_executor(agent.executor_type) and agent.executor_config_id:
        return "yellow"

    if agent.executor_type == "worker" and online_worker_available:
        return "yellow"

    own_status = (agent.status or "").lower()
    if own_status in ("online", "busy"):
        return "yellow"

    return "gray"


def resolve_agent_runtime_status(
    agent: "Agent",
    *,
    current_task: "Task | None" = None,
    online_worker_available: bool = False,
) -> str:
    """Return the canonical runtime status string: online / busy / offline."""
    from backend.src.models import TaskStatus

    if current_task is not None and current_task.status in (TaskStatus.running, TaskStatus.blocked):
        return "busy"

    if _is_llm_api_executor(agent.executor_type) and agent.executor_config_id:
        return "online"

    if agent.executor_type == "worker":
        return "online" if online_worker_available else "offline"

    own_status = (agent.status or "").lower()
    return own_status or "offline"
