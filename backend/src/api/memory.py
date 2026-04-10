"""Agent long-term memory API.

Routes auto-select the right backend per tenant: LocalMemoryProvider
by default, BSageMemoryProvider when the active tenant has BSage
credentials in its settings.
"""

from __future__ import annotations

import uuid
from typing import Any

from bsvibe_auth import BSVibeUser
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.auth import Permission, require_permission
from backend.src.core.memory import MemoryProvider, MemoryRecord, make_memory_provider
from backend.src.core.tenant_context import get_tenant_id
from backend.src.models import Tenant
from backend.src.storage.database import get_db

router = APIRouter(prefix="/api/v1/projects/{project_id}/memories", tags=["memory"])


async def _resolve_provider(db: AsyncSession, tenant_id: uuid.UUID) -> MemoryProvider:
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    settings = tenant.settings if tenant else None
    return make_memory_provider(db, settings)


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
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> list[MemoryResponse]:
    provider = await _resolve_provider(db, tenant_id)
    records = await provider.recall(project_id, agent_id=agent_id, category=category, limit=limit)
    return [_to_response(r) for r in records]


@router.post("", response_model=MemoryResponse, status_code=201)
async def create_memory(
    project_id: uuid.UUID,
    body: MemoryCreate,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_update)),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> MemoryResponse:
    provider = await _resolve_provider(db, tenant_id)
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
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> None:
    provider = await _resolve_provider(db, tenant_id)
    deleted = await provider.forget(memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found")
    await db.commit()
