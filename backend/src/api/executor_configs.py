"""ExecutorConfigs API — tenant-scoped CRUD.

An ``ExecutorConfig`` is a registered executor that runs LLM calls for
the tenant: a LiteLLM-direct config (``llm_api``), a BSGateway
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

from backend.src.config import settings as app_settings
from backend.src.core.auth import get_current_user
from backend.src.core.encryption import EncryptionManager
from backend.src.core.tenant_context import get_tenant_id
from backend.src.models import ExecutorConfig
from backend.src.schemas.executor_config import (
    EXECUTOR_TYPES,
    SENSITIVE_CONFIG_KEYS,
    ExecutorConfigCreate,
    ExecutorConfigResponse,
    ExecutorConfigUpdate,
    _redact_config,
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


def _encryption() -> EncryptionManager:
    return EncryptionManager(app_settings.encryption_key)


def _split_sensitive(config: dict, explicit_api_key: str | None) -> tuple[dict, str | None]:
    """Pull the API-key plaintext out of ``config`` (or take the explicit
    ``api_key`` field) and return ``(redacted_config, plaintext_api_key)``.

    Precedence: explicit ``api_key`` field on the payload > legacy
    ``config["bsgateway_api_key"]`` > legacy ``config["api_key"]``.
    Plaintext, if found, is encrypted by the caller and stored on the
    column; the redacted config never contains the plaintext.
    """
    cleaned = {k: v for k, v in (config or {}).items() if k not in SENSITIVE_CONFIG_KEYS}
    if explicit_api_key is not None:
        return cleaned, explicit_api_key or None
    for key in ("bsgateway_api_key", "api_key"):
        value = (config or {}).get(key)
        if isinstance(value, str) and value:
            return cleaned, value
    return cleaned, None


def _to_response(row: ExecutorConfig) -> ExecutorConfigResponse:
    """Map an ORM row to the response schema, redacting the config dict
    and exposing only ``has_api_key`` instead of the encrypted blob."""
    return ExecutorConfigResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        name=row.name,
        executor_type=row.executor_type,
        config=_redact_config(row.config or {}),
        description=row.description,
        is_selected=row.is_selected,
        has_api_key=bool(row.api_key_encrypted),
        created_at=row.created_at,
        updated_at=row.updated_at,
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
) -> list[ExecutorConfigResponse]:
    stmt = select(ExecutorConfig).where(ExecutorConfig.tenant_id == tenant_id).order_by(ExecutorConfig.created_at.asc())
    rows = list((await db.execute(stmt)).scalars())
    return [_to_response(r) for r in rows]


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
) -> ExecutorConfigResponse:
    _validate_executor_type(payload.executor_type)

    cleaned_config, plaintext_key = _split_sensitive(payload.config or {}, payload.api_key)
    api_key_encrypted = _encryption().encrypt_value(plaintext_key) if plaintext_key else None

    row = ExecutorConfig(
        tenant_id=tenant_id,
        name=payload.name,
        executor_type=payload.executor_type,
        config=cleaned_config,
        api_key_encrypted=api_key_encrypted,
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
        has_api_key=bool(row.api_key_encrypted),
    )
    return _to_response(row)


@router.get("/{config_id}", response_model=ExecutorConfigResponse)
async def get_config(
    config_id: uuid.UUID,
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> ExecutorConfigResponse:
    row = await _get_for_tenant(db, config_id, tenant_id)
    return _to_response(row)


@router.patch("/{config_id}", response_model=ExecutorConfigResponse)
async def update_config(
    config_id: uuid.UUID,
    payload: ExecutorConfigUpdate,
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> ExecutorConfigResponse:
    row = await _get_for_tenant(db, config_id, tenant_id)

    data = payload.model_dump(exclude_unset=True)

    if data.get("is_selected") is True:
        await _unset_other_selected(db, tenant_id, keep_id=row.id)

    # Pull out the sensitive bits before generic setattr-loop runs.
    explicit_api_key = data.pop("api_key", None) if "api_key" in data else None
    incoming_config = data.pop("config", None)
    if incoming_config is not None or explicit_api_key is not None:
        cleaned_config, plaintext_key = _split_sensitive(
            incoming_config if incoming_config is not None else (row.config or {}),
            explicit_api_key,
        )
        if incoming_config is not None:
            row.config = cleaned_config
        if plaintext_key is not None:
            row.api_key_encrypted = _encryption().encrypt_value(plaintext_key)

    for key, value in data.items():
        setattr(row, key, value)

    await db.commit()
    await db.refresh(row)
    return _to_response(row)


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
