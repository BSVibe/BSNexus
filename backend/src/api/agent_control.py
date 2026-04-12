"""Agent control API — stop-all and other agent lifecycle operations."""

from __future__ import annotations

import uuid

from bsvibe_auth import BSVibeUser
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.auth import Permission, require_permission
from backend.src.storage.database import get_db
from backend.src.storage.redis_client import get_redis
from backend.src.tools.cancellation import cancel_project_agents

router = APIRouter(prefix="/api/v1/projects/{project_id}/agents", tags=["agent-control"])


@router.post("/stop-all")
async def stop_all_agents(
    project_id: uuid.UUID,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_update)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Stop all running agents in a project.

    1. Cancel all tracked background asyncio.Tasks
    2. Set CancellationToken so executor loop stops
    3. Clear busy state from Redis
    4. Publish cancel signal to workers
    """
    from backend.src.api.agent_chat import _project_tasks
    from backend.src.core.agent_activity import clear_agent_busy
    from backend.src.core.tenant_context import get_tenant_id
    import redis.asyncio as aioredis

    redis = await get_redis()

    # Cancel background tasks
    tasks = _project_tasks.pop(project_id, set())
    cancelled_count = 0
    for t in tasks:
        if not t.done():
            t.cancel()
            cancelled_count += 1

    # Clear all busy keys for this project's tenant
    if redis:
        keys = await redis.keys("agent_busy:*")
        for k in keys:
            await redis.delete(k)

    result = await cancel_project_agents(project_id, redis)
    result["tasks_cancelled"] = cancelled_count
    return result
