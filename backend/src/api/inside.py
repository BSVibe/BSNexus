"""Inside panel APIs — flat resource shape (decision-locks A3, 2026-05-08).

- ``GET /api/v1/runs?request_id={id}``               — execution runs for a request
- ``GET /api/v1/runs/{id}/activities``               — activity timeline for one run (PR7)
- ``GET /api/v1/composition-snapshots/{snapshot_id}`` — composition snapshot detail

Always tenant-scoped; never expose another tenant's runs/snapshots.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.auth import get_current_user
from backend.src.core.tenant_context import get_tenant_id
from backend.src.models import (
    CompositionSnapshot,
    ExecutionRun,
    ExecutionRunActivity,
    Request,
)
from backend.src.models.execution_run_activity import ActivityLevel
from backend.src.schemas import CompositionSnapshotResponse, ExecutionRunResponse
from backend.src.storage.database import get_db


class ActivityResponse(BaseModel):
    """One ExecutionRunActivity row — Inside-panel timeline format."""

    id: uuid.UUID
    run_id: uuid.UUID
    project_id: uuid.UUID
    level: ActivityLevel
    event_type: str
    summary: str
    detail: dict[str, Any] | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

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


@runs_router.get(
    "/{run_id}/activities",
    response_model=list[ActivityResponse],
)
async def list_run_activities(
    run_id: uuid.UUID,
    level: ActivityLevel | None = Query(default=None, description="Filter by milestone or tool."),
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> list[ExecutionRunActivity]:
    """Activity timeline for one ExecutionRun (PR7).

    Tenant-scoped via the run's tenant — fetch the run first to enforce
    isolation before listing its activities. Cross-tenant lookup → 404.
    """
    run = (
        await db.execute(
            select(ExecutionRun).where(
                ExecutionRun.id == run_id,
                ExecutionRun.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    stmt = (
        select(ExecutionRunActivity)
        .where(ExecutionRunActivity.run_id == run_id)
        .order_by(ExecutionRunActivity.created_at.asc())
    )
    if level is not None:
        stmt = stmt.where(ExecutionRunActivity.level == level)
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
