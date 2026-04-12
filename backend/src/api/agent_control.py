"""Agent control API — stop-all and other agent lifecycle operations."""

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

    1. Transition all running tasks to blocked via state machine
    2. Cancel tracked background asyncio.Tasks
    3. State machine publishes task_transition SSE events automatically
    """
    from backend.src.api.agent_chat import _project_tasks

    redis = await get_redis()
    stream_manager = RedisStreamManager(redis) if redis else None
    sm = TaskStateMachine()

    # Transition all running tasks to blocked
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
            pass  # Already transitioned
    await db.commit()

    # Cancel background asyncio tasks
    tasks = _project_tasks.pop(project_id, set())
    cancelled_count = 0
    for t in tasks:
        if not t.done():
            t.cancel()
            cancelled_count += 1

    return {
        "project_id": str(project_id),
        "tasks_blocked": blocked_count,
        "tasks_cancelled": cancelled_count,
    }
