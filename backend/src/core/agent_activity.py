"""Transient per-agent activity tracking + unified status resolution.

The persistent ``Agent.status`` column only carries the slow-moving values
that the heartbeat / executor lifecycle stamps (``online`` / ``offline`` /
``busy``). It is the wrong place to store transient state like *"this
agent is mid-chat right now"* — that flips on the order of seconds and
needs to clear automatically if the worker process crashes.

This module owns three concerns that are otherwise duplicated across
``api/agents.py`` (Agents tab) and ``api/plan_tree.py`` (Plan view):

1. ``BusyAgentTracker`` — Redis-backed transient set with a TTL. Marks
   an agent as actively chatting; auto-expires so a crash never leaves
   the UI showing a permanent ``thinking`` state.
2. ``has_online_worker`` — does *any* registered worker in this tenant
   have an online status? Worker-typed agents inherit availability from
   the worker pool, not from their own ``Agent.status`` column.
3. ``resolve_agent_status_dot`` — single source of truth for the
   five-state status dot. Both endpoints call this so the Agents tab
   and the Plan view never disagree about whether an agent is online.

Status colours
--------------

* ``red``    — has a *blocked* task assigned (needs human attention)
* ``green``  — has a *running* task assigned (worker is doing real work)
* ``blue``   — currently mid-chat (transient busy from BusyAgentTracker)
* ``yellow`` — online and idle
* ``gray``   — offline / unreachable
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from backend.src import models
    from backend.src.models import Agent, Task

# ── BusyAgentTracker (Redis) ────────────────────────────────────────

# Window long enough to cover a slow chat turn but short enough that a
# crashed worker / dropped connection clears the indicator on its own.
# Must outlive the longest possible worker turn (design / coding can
# take 30+ minutes). Auto-expires so a crash never leaves the UI stuck.
BUSY_TTL_SECONDS = 2400  # 40 minutes


def _busy_key(tenant_id: uuid.UUID, agent_id: uuid.UUID) -> str:
    return f"agent_busy:{tenant_id}:{agent_id}"


async def mark_agent_busy(
    redis: Any | None,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    activity: str = "",
) -> None:
    """Stamp the agent as actively chatting, with an optional activity summary.

    ``activity`` is a short human-readable string describing what the
    agent is doing ("시장 조사 중...", "코드 리뷰 진행 중..."). The
    frontend reads it from the ``activity`` field on ``AgentResponse``
    and displays it in the status bar instead of a generic "thinking".
    """
    if redis is None:
        return
    value = activity[:120] if activity else "1"
    try:
        await redis.set(_busy_key(tenant_id, agent_id), value, ex=BUSY_TTL_SECONDS)
    except Exception:  # noqa: BLE001 — best-effort transient state
        pass


async def clear_agent_busy(
    redis: Any | None, tenant_id: uuid.UUID, agent_id: uuid.UUID
) -> None:
    if redis is None:
        return
    try:
        await redis.delete(_busy_key(tenant_id, agent_id))
    except Exception:  # noqa: BLE001
        pass


async def busy_agent_activities(
    redis: Any | None, tenant_id: uuid.UUID, agent_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """Return ``{agent_id: activity_summary}`` for agents that are currently busy.

    A single MGET keeps this O(1) on the wire regardless of N.
    The activity string is whatever was passed to ``mark_agent_busy``;
    it may be ``"1"`` for legacy callers that didn't supply a summary.
    """
    if redis is None or not agent_ids:
        return {}
    keys = [_busy_key(tenant_id, aid) for aid in agent_ids]
    try:
        values = await redis.mget(keys)
    except Exception:  # noqa: BLE001
        return {}
    result: dict[uuid.UUID, str] = {}
    for aid, value in zip(agent_ids, values, strict=True):
        if value is not None:
            decoded = value.decode() if isinstance(value, bytes) else str(value)
            result[aid] = decoded
    return result


async def busy_agent_ids(
    redis: Any | None, tenant_id: uuid.UUID, agent_ids: list[uuid.UUID]
) -> set[uuid.UUID]:
    """Backward-compat wrapper — returns just the set of busy ids."""
    return set((await busy_agent_activities(redis, tenant_id, agent_ids)).keys())


# ── Worker availability ─────────────────────────────────────────────


async def has_online_worker(db: AsyncSession, tenant_id: uuid.UUID) -> bool:
    """True if any active worker in the tenant is currently online."""
    from backend.src.models import Worker  # local import dodges cycles

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
    is_busy: bool = False,
    online_worker_available: bool = False,
) -> str:
    """Return the canonical status dot colour for an agent.

    Precedence (high → low):

    1. ``red``    — assigned a blocked task
    2. ``green``  — assigned a running task
    3. ``blue``   — actively chatting (transient busy)
    4. ``yellow`` — online + idle (own status, or worker pool has any
                    online worker for worker-typed agents, or LLM API
                    executor with config)
    5. ``gray``   — offline
    """
    from backend.src.models import TaskStatus

    if current_task is not None:
        if current_task.status == TaskStatus.blocked:
            return "red"
        return "green"

    if is_busy:
        return "green"

    own_status = (agent.status or "").lower()
    if own_status == "online":
        return "yellow"
    if own_status == "busy":
        return "green"

    # LLM API executors are always-on — they don't need heartbeat.
    # If the agent has an executor config, treat as online/idle.
    if _is_llm_api_executor(agent.executor_type) and agent.executor_config_id:
        return "yellow"

    if agent.executor_type == "worker" and online_worker_available:
        return "yellow"
    return "gray"


def resolve_agent_runtime_status(
    agent: "Agent",
    *,
    is_busy: bool = False,
    online_worker_available: bool = False,
) -> str:
    """Return the canonical runtime status string for an agent row.

    Mirrors ``resolve_agent_status_dot`` but for the textual status
    field on AgentResponse: ``online`` / ``busy`` / ``offline``.
    """
    if is_busy:
        return "busy"
    own_status = (agent.status or "").lower()
    if own_status in ("online", "busy"):
        return own_status

    # LLM API executors are always-on when configured.
    if _is_llm_api_executor(agent.executor_type) and agent.executor_config_id:
        return "online"

    if agent.executor_type == "worker":
        return "online" if online_worker_available else "offline"

    if own_status == "offline":
        return "offline"
    return own_status or "offline"
