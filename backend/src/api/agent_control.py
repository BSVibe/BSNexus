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

    Cancels in-process executions and notifies workers to stop.
    """
    redis = await get_redis()
    result = await cancel_project_agents(project_id, redis)
    return result
