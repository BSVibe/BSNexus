"""Run-summary surface — failure-mode dashboard backend (PR7 TASK-005).

Two query modes from a single endpoint, per A3 flat-REST shape:

- ``GET /api/v1/run-summaries?project_id=<uuid>&days=7&limit=50`` —
  list of recent runs with their per-run summary JSON. Default
  ordering: most recent first. Omit ``project_id`` for the tenant
  cross-project view.
- Same URL with ``aggregate=true`` — counts per
  ``dominant_reply_quality`` over the time window. Powers the
  Inside-tab failure-mode strip.

Tenant-scoped via ``Depends(get_tenant_id)``. Never returns rows
that belong to another tenant.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.auth import get_current_user
from backend.src.core.tenant_context import get_tenant_id
from backend.src.models import ExecutionRun, RunStatus
from backend.src.storage.database import get_db

router = APIRouter(prefix="/api/v1/run-summaries", tags=["instrumentation"])


class RunSummaryItem(BaseModel):
    """One row in the list view — minimal joinless payload."""

    run_id: uuid.UUID
    project_id: uuid.UUID
    status: RunStatus
    created_at: datetime
    completed_at: datetime | None
    summary: dict[str, Any] | None

    model_config = ConfigDict(from_attributes=True)


class AggregateResponse(BaseModel):
    """Count per ``dominant_reply_quality`` over the requested window."""

    window_days: int = Field(ge=1)
    total_runs: int = Field(ge=0)
    counts: dict[str, int] = Field(default_factory=dict)
    project_id: uuid.UUID | None = None


@router.get(
    "",
    status_code=status.HTTP_200_OK,
)
async def list_run_summaries(
    project_id: uuid.UUID | None = Query(default=None),
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=50, ge=1, le=500),
    aggregate: bool = Query(default=False),
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> Any:
    since = datetime.now(timezone.utc) - timedelta(days=days)

    base_filters = [
        ExecutionRun.tenant_id == tenant_id,
        ExecutionRun.created_at >= since,
    ]
    if project_id is not None:
        base_filters.append(ExecutionRun.project_id == project_id)

    if aggregate:
        # GROUP BY the JSON path expression; SQLite + PG both support
        # subscripting via dict-key access in SQLAlchemy ``[ ]`` ops.
        # Falls back to a Python-side count for portability across
        # the two test/prod backends — JSONB GROUP BY is PG-only and
        # the test suite runs on SQLite.
        stmt = select(ExecutionRun.run_summary).where(*base_filters)
        rows = (await db.execute(stmt)).scalars().all()
        counts: dict[str, int] = {}
        for summary in rows:
            kind = (summary or {}).get("dominant_reply_quality")
            if isinstance(kind, str):
                counts[kind] = counts.get(kind, 0) + 1
        return AggregateResponse(
            window_days=days,
            total_runs=len(rows),
            counts=counts,
            project_id=project_id,
        ).model_dump(mode="json")

    stmt = select(ExecutionRun).where(*base_filters).order_by(ExecutionRun.created_at.desc()).limit(limit)
    runs = (await db.execute(stmt)).scalars().all()
    return [
        RunSummaryItem(
            run_id=r.id,
            project_id=r.project_id,
            status=r.status,
            created_at=r.created_at,
            completed_at=r.completed_at,
            summary=r.run_summary,
        ).model_dump(mode="json")
        for r in runs
    ]


__all__ = ["router", "RunSummaryItem", "AggregateResponse"]
