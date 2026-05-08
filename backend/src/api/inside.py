"""Inside panel APIs — flat resource shape (decision-locks A3, 2026-05-08).

- ``GET /api/v1/runs?request_id={id}``               — execution runs for a request
- ``GET /api/v1/composition-snapshots/{snapshot_id}`` — composition snapshot detail

Always tenant-scoped; never expose another tenant's runs/snapshots.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.auth import get_current_user
from backend.src.core.tenant_context import get_tenant_id
from backend.src.models import CompositionSnapshot, ExecutionRun, Request
from backend.src.schemas import CompositionSnapshotResponse, ExecutionRunResponse
from backend.src.storage.database import get_db

runs_router = APIRouter(prefix="/api/v1/runs", tags=["inside"])
snapshot_router = APIRouter(prefix="/api/v1/composition-snapshots", tags=["inside"])


async def _require_request(db: AsyncSession, request_id: uuid.UUID, tenant_id: uuid.UUID) -> Request:
    stmt = select(Request).where(Request.id == request_id, Request.tenant_id == tenant_id)
    req = (await db.execute(stmt)).scalar_one_or_none()
    if req is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Request not found")
    return req


@runs_router.get(
    "",
    response_model=list[ExecutionRunResponse],
)
async def list_runs(
    request_id: uuid.UUID = Query(..., description="Request whose runs to list."),
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> list[ExecutionRun]:
    await _require_request(db, request_id, tenant_id)
    stmt = select(ExecutionRun).where(ExecutionRun.request_id == request_id).order_by(ExecutionRun.created_at.asc())
    return list((await db.execute(stmt)).scalars())


@snapshot_router.get(
    "/{snapshot_id}",
    response_model=CompositionSnapshotResponse,
)
async def get_composition_snapshot(
    snapshot_id: uuid.UUID,
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> CompositionSnapshot:
    stmt = select(CompositionSnapshot).where(
        CompositionSnapshot.id == snapshot_id,
        CompositionSnapshot.tenant_id == tenant_id,
    )
    snapshot = (await db.execute(stmt)).scalar_one_or_none()
    if snapshot is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Composition snapshot not found")
    return snapshot
