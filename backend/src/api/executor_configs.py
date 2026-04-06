"""Executor Config CRUD API — register, list, update, delete executor instances."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.tenant_context import DEFAULT_TENANT_ID
from backend.src.models.executor_config import ExecutorConfig
from backend.src.schemas.executor_config import (
    EXECUTOR_TYPES,
    ExecutorConfigCreate,
    ExecutorConfigResponse,
    ExecutorConfigUpdate,
)
from backend.src.storage.database import get_db

router = APIRouter(prefix="/api/v1/executor-configs", tags=["executor-configs"])


@router.post("", response_model=ExecutorConfigResponse, status_code=201)
async def create_executor_config(
    body: ExecutorConfigCreate, db: AsyncSession = Depends(get_db)
) -> ExecutorConfigResponse:
    """Register a new executor configuration."""
    if body.executor_type not in EXECUTOR_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid executor_type '{body.executor_type}'. Valid: {sorted(EXECUTOR_TYPES)}",
        )

    # If this is set as default, unset existing defaults for same executor_type
    if body.is_default:
        await db.execute(
            update(ExecutorConfig)
            .where(
                ExecutorConfig.tenant_id == DEFAULT_TENANT_ID,
                ExecutorConfig.executor_type == body.executor_type,
                ExecutorConfig.is_default.is_(True),
            )
            .values(is_default=False)
        )

    config = ExecutorConfig(
        tenant_id=DEFAULT_TENANT_ID,
        name=body.name,
        executor_type=body.executor_type,
        config=body.config,
        description=body.description,
        is_default=body.is_default,
    )
    db.add(config)
    await db.flush()
    await db.commit()
    await db.refresh(config)
    return ExecutorConfigResponse.model_validate(config)


@router.get("", response_model=list[ExecutorConfigResponse])
async def list_executor_configs(db: AsyncSession = Depends(get_db)) -> list[ExecutorConfigResponse]:
    """List all registered executor configurations."""
    result = await db.execute(
        select(ExecutorConfig)
        .where(ExecutorConfig.tenant_id == DEFAULT_TENANT_ID)
        .order_by(ExecutorConfig.executor_type, ExecutorConfig.name)
    )
    return [ExecutorConfigResponse.model_validate(c) for c in result.scalars().all()]


@router.get("/{config_id}", response_model=ExecutorConfigResponse)
async def get_executor_config(
    config_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> ExecutorConfigResponse:
    result = await db.execute(select(ExecutorConfig).where(ExecutorConfig.id == config_id))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Executor config not found")
    return ExecutorConfigResponse.model_validate(config)


@router.patch("/{config_id}", response_model=ExecutorConfigResponse)
async def update_executor_config(
    config_id: uuid.UUID, body: ExecutorConfigUpdate, db: AsyncSession = Depends(get_db)
) -> ExecutorConfigResponse:
    result = await db.execute(select(ExecutorConfig).where(ExecutorConfig.id == config_id))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Executor config not found")

    update_data = body.model_dump(exclude_unset=True)

    # If setting as default, unset existing defaults for same type
    if update_data.get("is_default"):
        await db.execute(
            update(ExecutorConfig)
            .where(
                ExecutorConfig.tenant_id == DEFAULT_TENANT_ID,
                ExecutorConfig.executor_type == config.executor_type,
                ExecutorConfig.is_default.is_(True),
                ExecutorConfig.id != config_id,
            )
            .values(is_default=False)
        )

    for key, value in update_data.items():
        setattr(config, key, value)

    await db.flush()
    await db.commit()
    await db.refresh(config)
    return ExecutorConfigResponse.model_validate(config)


@router.delete("/{config_id}", status_code=204)
async def delete_executor_config(config_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> None:
    result = await db.execute(select(ExecutorConfig).where(ExecutorConfig.id == config_id))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Executor config not found")
    await db.delete(config)
    await db.commit()
