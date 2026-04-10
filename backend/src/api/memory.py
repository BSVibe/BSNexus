"""Agent long-term memory API.

Routes for the local provider. Projects that opt into BSage will swap
in a different ``MemoryProvider`` implementation behind these endpoints.
"""

from __future__ import annotations

import uuid
from typing import Any

from bsvibe_auth import BSVibeUser
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.auth import Permission, require_permission
from backend.src.core.memory import LocalMemoryProvider, MemoryRecord
from backend.src.storage.database import get_db

router = APIRouter(prefix="/api/v1/projects/{project_id}/memories", tags=["memory"])


class MemoryCreate(BaseModel):
    agent_id: uuid.UUID | None = None
    category: str = "decision"
    title: str
    content: str
    metadata: dict[str, Any] | None = None


class MemoryResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    agent_id: uuid.UUID | None = None
    category: str
    title: str
    content: str
    metadata: dict[str, Any] | None = None


def _to_response(record: MemoryRecord) -> MemoryResponse:
    return MemoryResponse(
        id=record.id,
        project_id=record.project_id,
        agent_id=record.agent_id,
        category=record.category,
        title=record.title,
        content=record.content,
        metadata=record.metadata,
    )


@router.get("", response_model=list[MemoryResponse])
async def list_memories(
    project_id: uuid.UUID,
    agent_id: uuid.UUID | None = Query(None),
    category: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    _auth: BSVibeUser = Depends(require_permission(Permission.project_read)),
    db: AsyncSession = Depends(get_db),
) -> list[MemoryResponse]:
    provider = LocalMemoryProvider(db)
    records = await provider.recall(project_id, agent_id=agent_id, category=category, limit=limit)
    return [_to_response(r) for r in records]


@router.post("", response_model=MemoryResponse, status_code=201)
async def create_memory(
    project_id: uuid.UUID,
    body: MemoryCreate,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_update)),
    db: AsyncSession = Depends(get_db),
) -> MemoryResponse:
    provider = LocalMemoryProvider(db)
    record = await provider.remember(
        project_id,
        body.agent_id,
        category=body.category,
        title=body.title,
        content=body.content,
        metadata=body.metadata,
    )
    await db.commit()
    return _to_response(record)


@router.delete("/{memory_id}", status_code=204)
async def delete_memory(
    project_id: uuid.UUID,
    memory_id: uuid.UUID,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_update)),
    db: AsyncSession = Depends(get_db),
) -> None:
    provider = LocalMemoryProvider(db)
    deleted = await provider.forget(memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found")
    await db.commit()
