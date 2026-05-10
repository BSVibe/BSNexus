"""Per-tenant executor (LLM dispatch) admin API.

One config per tenant. ``kind`` switches between:
  - ``bsgateway`` (BSGateway worker pool — base_url + registration token)
  - ``llm_api`` (litellm direct — base_url + model + provider key)

Switching kind is a UPSERT on the same row, not a new row.
``api_key`` on PUT is tri-state:
  - omitted → preserve existing encrypted value
  - null    → clear the secret
  - string  → encrypt + replace
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.config import settings as app_settings
from backend.src.core.auth import get_current_user
from backend.src.core.encryption import EncryptionManager
from backend.src.core.tenant_context import get_tenant_id
from backend.src.models.executor_config import ExecutorConfig
from backend.src.schemas.executor import (
    ExecutorConfigResponse,
    ExecutorConfigUpdate,
    redacted,
)
from backend.src.storage.database import get_db

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/executor-config", tags=["executor-config"])


def _encryption() -> EncryptionManager:
    return EncryptionManager(app_settings.encryption_key)


async def _load_row(db: AsyncSession, tenant_id: uuid.UUID) -> ExecutorConfig | None:
    stmt = select(ExecutorConfig).where(ExecutorConfig.tenant_id == tenant_id)
    return (await db.execute(stmt)).scalar_one_or_none()


@router.get("", response_model=ExecutorConfigResponse | None)
async def get_executor_config(
    response: Response,
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> ExecutorConfigResponse | None:
    row = await _load_row(db, tenant_id)
    return redacted(row)


@router.put("", response_model=ExecutorConfigResponse)
async def upsert_executor_config(
    payload: ExecutorConfigUpdate,
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> ExecutorConfigResponse:
    row = await _load_row(db, tenant_id)
    if row is None:
        row = ExecutorConfig(tenant_id=tenant_id, kind=payload.kind)
        db.add(row)

    updates = payload.model_dump(exclude_unset=True)

    row.kind = payload.kind
    if "base_url" in updates:
        row.base_url = updates["base_url"] or None
    if "model" in updates:
        row.model = updates["model"] or None
    if "extra_config" in updates:
        row.extra_config = updates["extra_config"] or {}
    if "api_key" in updates:
        if updates["api_key"]:
            row.api_key_encrypted = _encryption().encrypt_value(updates["api_key"])
        else:
            row.api_key_encrypted = None

    await db.commit()
    await db.refresh(row)
    logger.info(
        "executor_config_upserted",
        tenant_id=str(tenant_id),
        kind=row.kind.value,
    )
    result = redacted(row)
    assert result is not None
    return result
