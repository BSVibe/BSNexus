"""Executor Config CRUD API — register, list, update, delete executor instances."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.tenant_context import get_tenant_id
from backend.src.models.agent import Agent
from backend.src.models.executor_config import ExecutorConfig
from backend.src.schemas.executor_config import (
    EXECUTOR_TYPES,
    ExecutorConfigCreate,
    ExecutorConfigResponse,
    ExecutorConfigUpdate,
)
from backend.src.storage.database import get_db

router = APIRouter(prefix="/api/v1/executor-configs", tags=["executor-configs"])


async def _cascade_default_to_using_agents(
    db: AsyncSession, tenant_id: uuid.UUID, executor_type: str
) -> None:
    """Update every "use default" agent in the tenant to the new default's type.

    Agents with ``executor_config_id IS NULL`` mean *"whatever the tenant
    default is right now"*. The agent row caches the resolved ``executor_type``
    so dispatcher / status code can read it without joining ``executor_configs``
    on every read; that cache must be re-synced any time the tenant default
    changes, otherwise the agent stays frozen at whatever default existed at
    creation time and never picks up a newly registered worker.
    """
    await db.execute(
        update(Agent)
        .where(
            Agent.tenant_id == tenant_id,
            Agent.executor_config_id.is_(None),
        )
        .values(executor_type=executor_type)
    )


@router.post("", response_model=ExecutorConfigResponse, status_code=201)
async def create_executor_config(
    body: ExecutorConfigCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> ExecutorConfigResponse:
    """Register a new executor configuration."""
    if body.executor_type not in EXECUTOR_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid executor_type '{body.executor_type}'. Valid: {sorted(EXECUTOR_TYPES)}",
        )

    # If this is set as default, unset ALL existing defaults for this tenant
    if body.is_default:
        await db.execute(
            update(ExecutorConfig)
            .where(
                ExecutorConfig.tenant_id == tenant_id,
                ExecutorConfig.is_default.is_(True),
            )
            .values(is_default=False)
        )

    config = ExecutorConfig(
        tenant_id=tenant_id,
        name=body.name,
        executor_type=body.executor_type,
        config=body.config,
        description=body.description,
        is_default=body.is_default,
    )
    db.add(config)
    await db.flush()
    if body.is_default:
        await _cascade_default_to_using_agents(db, tenant_id, body.executor_type)
        # Also bind agents that have no executor_config_id to this default.
        await db.execute(
            update(Agent)
            .where(
                Agent.tenant_id == tenant_id,
                Agent.executor_config_id.is_(None),
            )
            .values(executor_config_id=config.id)
        )
    await db.commit()
    await db.refresh(config)
    return ExecutorConfigResponse.model_validate(config)


@router.get("", response_model=list[ExecutorConfigResponse])
async def list_executor_configs(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> list[ExecutorConfigResponse]:
    """List all registered executor configurations."""
    result = await db.execute(
        select(ExecutorConfig)
        .where(ExecutorConfig.tenant_id == tenant_id)
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
    config_id: uuid.UUID,
    body: ExecutorConfigUpdate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> ExecutorConfigResponse:
    result = await db.execute(select(ExecutorConfig).where(ExecutorConfig.id == config_id))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Executor config not found")

    update_data = body.model_dump(exclude_unset=True)

    # If setting as default, unset ALL existing defaults for this tenant
    if update_data.get("is_default"):
        await db.execute(
            update(ExecutorConfig)
            .where(
                ExecutorConfig.tenant_id == tenant_id,
                ExecutorConfig.is_default.is_(True),
                ExecutorConfig.id != config_id,
            )
            .values(is_default=False)
        )

    for key, value in update_data.items():
        setattr(config, key, value)

    await db.flush()
    if update_data.get("is_default"):
        # Re-sync every "use default" agent to the new default's executor type.
        await _cascade_default_to_using_agents(db, tenant_id, config.executor_type)
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
