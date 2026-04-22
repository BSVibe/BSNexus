"""Agent control API — stop-all, restart, and other agent lifecycle operations."""

from __future__ import annotations

import uuid

from bsvibe_auth import BSVibeUser
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.auth import Permission, require_permission
from backend.src.core.state_machine import TaskStateMachine
from backend.src.models import Task, TaskStatus
from backend.src.queue.streams import RedisStreamManager
from backend.src.storage.database import get_db
from backend.src.storage.redis_client import get_redis
from backend.src.core.tenant_context import get_tenant_id
from backend.src.tools.cancellation import CancellationToken

router = APIRouter(prefix="/api/v1/projects/{project_id}/agents", tags=["agent-control"])


@router.post("/stop-all")
async def stop_all_agents(
    project_id: uuid.UUID,
    request: Request,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_update)),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> dict:
    """Stop all running agents in a project.

    1. Set CancellationToken so executors exit their LLM loop
    2. Cancel queued work via AgentQueueManager
    3. Transition all running tasks to blocked via state machine
    """
    from backend.src.core.agent_queue import get_agent_queue_manager

    redis = await get_redis()
    stream_manager = RedisStreamManager(redis) if redis else None
    sm = TaskStateMachine()

    # 1. Pause dispatcher for this project (no new dispatches)
    dispatcher = getattr(request.app.state, "global_dispatcher", None)
    if dispatcher is not None:
        dispatcher.pause_project(project_id)

    # 2. Signal cancellation to any in-flight executor loops
    CancellationToken.cancel(project_id)

    # 2. Cancel queued and in-progress agent work
    try:
        mgr = get_agent_queue_manager()
        queue_cancelled = await mgr.cancel_project(project_id)
    except RuntimeError:
        queue_cancelled = 0

    # 3. Transition all running tasks to blocked
    result = await db.execute(
        select(Task).where(
            Task.project_id == project_id,
            Task.status == TaskStatus.running,
        )
    )
    running_tasks = list(result.scalars().all())
    blocked_count = 0
    for task in running_tasks:
        try:
            await sm.transition(
                task, TaskStatus.blocked,
                actor="user:stop",
                reason="사용자가 중지",
                db_session=db,
                stream_manager=stream_manager,
            )
            blocked_count += 1
        except ValueError:
            pass
    await db.commit()

    # 4. Clear cancellation token so future requests work
    CancellationToken.reset(project_id)

    return {
        "project_id": str(project_id),
        "tasks_blocked": blocked_count,
        "queue_cancelled": queue_cancelled,
    }


@router.post("/restart")
async def restart_agents(
    project_id: uuid.UUID,
    request: Request,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_update)),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> dict:
    """Restart stopped agents — transition blocked tasks back to pending.

    The global dispatcher will pick up pending tasks and dispatch them
    to available agents automatically.
    """
    redis = await get_redis()
    stream_manager = RedisStreamManager(redis) if redis else None
    sm = TaskStateMachine()

    # Resume dispatcher for this project
    dispatcher = getattr(request.app.state, "global_dispatcher", None)
    if dispatcher is not None:
        dispatcher.resume_project(project_id)

    # Clear any lingering cancellation
    CancellationToken.reset(project_id)

    # Transition blocked tasks back to pending
    result = await db.execute(
        select(Task).where(
            Task.project_id == project_id,
            Task.status == TaskStatus.blocked,
        )
    )
    blocked_tasks = list(result.scalars().all())
    restarted_count = 0
    for task in blocked_tasks:
        try:
            await sm.transition(
                task, TaskStatus.pending,
                actor="user:restart",
                reason="사용자가 재시작",
                db_session=db,
                stream_manager=stream_manager,
            )
            restarted_count += 1
        except ValueError:
            pass
    await db.commit()

    return {
        "project_id": str(project_id),
        "tasks_restarted": restarted_count,
    }
