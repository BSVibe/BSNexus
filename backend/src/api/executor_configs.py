"""ExecutorConfigs API — tenant-scoped CRUD.

An ``ExecutorConfig`` is a registered executor that runs LLM calls for
the tenant: a LiteLLM-direct config (``generic_llm``), a BSGateway
proxy config (``bsgateway``), a worker adapter (``worker`` /
``claude_code`` / ``codex``), etc. Tenants can register many configs
but exactly one carries ``is_selected = true`` — that's the one the
orchestrator consults when dispatching runs.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.auth import get_current_user
from backend.src.core.tenant_context import get_tenant_id
from backend.src.models import ExecutorConfig
from backend.src.schemas.executor_config import (
    EXECUTOR_TYPES,
    ExecutorConfigCreate,
    ExecutorConfigResponse,
    ExecutorConfigUpdate,
)
from backend.src.storage.database import get_db

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/executor-configs", tags=["executor-configs"])


def _validate_executor_type(executor_type: str) -> None:
    if executor_type not in EXECUTOR_TYPES:
        raise HTTPException(
            422,
            f"Unknown executor_type '{executor_type}'. Expected one of: {', '.join(sorted(EXECUTOR_TYPES))}",
        )


async def _unset_other_selected(db: AsyncSession, tenant_id: uuid.UUID, keep_id: uuid.UUID | None) -> None:
    stmt = (
        update(ExecutorConfig)
        .where(
            ExecutorConfig.tenant_id == tenant_id,
            ExecutorConfig.is_selected.is_(True),
        )
        .values(is_selected=False)
    )
    if keep_id is not None:
        stmt = stmt.where(ExecutorConfig.id != keep_id)
    await db.execute(stmt)


async def _get_for_tenant(db: AsyncSession, config_id: uuid.UUID, tenant_id: uuid.UUID) -> ExecutorConfig:
    stmt = select(ExecutorConfig).where(
        ExecutorConfig.id == config_id,
        ExecutorConfig.tenant_id == tenant_id,
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ExecutorConfig not found")
    return row


@router.get("", response_model=list[ExecutorConfigResponse])
async def list_configs(
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> list[ExecutorConfig]:
    stmt = select(ExecutorConfig).where(ExecutorConfig.tenant_id == tenant_id).order_by(ExecutorConfig.created_at.asc())
    return list((await db.execute(stmt)).scalars())


@router.post(
    "",
    response_model=ExecutorConfigResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_config(
    payload: ExecutorConfigCreate,
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> ExecutorConfig:
    _validate_executor_type(payload.executor_type)

    row = ExecutorConfig(
        tenant_id=tenant_id,
        name=payload.name,
        executor_type=payload.executor_type,
        config=payload.config or {},
        description=payload.description,
        is_selected=payload.is_selected,
    )
    if payload.is_selected:
        await _unset_other_selected(db, tenant_id, keep_id=None)

    db.add(row)
    await db.commit()
    await db.refresh(row)
    logger.info(
        "executor_config_created",
        tenant_id=str(tenant_id),
        config_id=str(row.id),
        executor_type=row.executor_type,
    )
    return row


@router.get("/{config_id}", response_model=ExecutorConfigResponse)
async def get_config(
    config_id: uuid.UUID,
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> ExecutorConfig:
    return await _get_for_tenant(db, config_id, tenant_id)


@router.patch("/{config_id}", response_model=ExecutorConfigResponse)
async def update_config(
    config_id: uuid.UUID,
    payload: ExecutorConfigUpdate,
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> ExecutorConfig:
    row = await _get_for_tenant(db, config_id, tenant_id)

    data = payload.model_dump(exclude_unset=True)

    if data.get("is_selected") is True:
        await _unset_other_selected(db, tenant_id, keep_id=row.id)

    for key, value in data.items():
        setattr(row, key, value)

    await db.commit()
    await db.refresh(row)
    return row


@router.delete("/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_config(
    config_id: uuid.UUID,
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> None:
    row = await _get_for_tenant(db, config_id, tenant_id)
    await db.delete(row)
    await db.commit()
