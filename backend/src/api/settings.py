from __future__ import annotations

import hashlib
import secrets
import uuid

from bsvibe_auth import BSVibeUser
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src import models, schemas
from backend.src.core.auth import Permission, require_permission
from backend.src.core.tenant_context import get_tenant_id
from backend.src.storage.database import get_db

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])

_LLM_SETTING_KEYS = ("llm_api_key", "llm_model", "llm_base_url", "default_executor_type")


async def get_raw_llm_config(db: AsyncSession) -> dict[str, str]:
    """Read global LLM settings from DB as a plain dict (unmasked)."""
    result = await db.execute(select(models.Setting).where(models.Setting.key.in_(_LLM_SETTING_KEYS)))
    return {s.key: s.value for s in result.scalars().all()}


def mask_api_key(key: str | None) -> str | None:
    """Mask API key for display: sk-ant-abc...xyz -> sk-****...xyz"""
    if not key or len(key) < 8:
        return key
    return key[:3] + "****..." + key[-4:]


@router.get("", response_model=schemas.GlobalSettingsResponse)
async def get_settings(
    _auth: BSVibeUser = Depends(require_permission(Permission.admin_settings)),
    db: AsyncSession = Depends(get_db),
) -> schemas.GlobalSettingsResponse:
    """Return global LLM settings with masked API key."""
    result = await db.execute(select(models.Setting))
    settings_map: dict[str, str] = {s.key: s.value for s in result.scalars().all()}

    return schemas.GlobalSettingsResponse(
        llm_api_key=mask_api_key(settings_map.get("llm_api_key")),
        llm_model=settings_map.get("llm_model"),
        llm_base_url=settings_map.get("llm_base_url"),
        default_executor_type=settings_map.get("default_executor_type", "generic_llm"),
    )


@router.put("", response_model=schemas.GlobalSettingsResponse)
async def update_settings(
    body: schemas.GlobalSettingsUpdate,
    _auth: BSVibeUser = Depends(require_permission(Permission.admin_settings)),
    db: AsyncSession = Depends(get_db),
) -> schemas.GlobalSettingsResponse:
    """Upsert global LLM settings. Returns the updated settings with masked API key."""
    for field_name, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            existing = await db.execute(select(models.Setting).where(models.Setting.key == field_name))
            setting = existing.scalar_one_or_none()
            if setting:
                setting.value = value
            else:
                db.add(models.Setting(key=field_name, value=value))

    await db.commit()

    # Return updated settings (with masking)
    return await get_settings(_auth=_auth, db=db)


# ─── Worker Install Token ────────────────────────────────────────

def _hash_install_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class InstallTokenResponse(BaseModel):
    token: str | None = None  # plaintext only on create; masked on GET
    has_token: bool = False


async def _get_tenant(db: AsyncSession, tenant_id: uuid.UUID) -> models.Tenant:
    result = await db.execute(select(models.Tenant).where(models.Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


@router.get("/install-token", response_model=InstallTokenResponse)
async def get_install_token(
    _auth: BSVibeUser = Depends(require_permission(Permission.admin_settings)),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> InstallTokenResponse:
    """Check if an install token exists for the active tenant."""
    tenant = await _get_tenant(db, tenant_id)
    return InstallTokenResponse(has_token=tenant.worker_install_token_hash is not None)


@router.post("/install-token", response_model=InstallTokenResponse)
async def create_install_token(
    _auth: BSVibeUser = Depends(require_permission(Permission.admin_settings)),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> InstallTokenResponse:
    """Generate a new install token for this tenant (replaces any existing)."""
    tenant = await _get_tenant(db, tenant_id)
    token = f"bsn-{secrets.token_urlsafe(32)}"
    tenant.worker_install_token_hash = _hash_install_token(token)
    await db.commit()
    return InstallTokenResponse(token=token, has_token=True)


@router.delete("/install-token", status_code=204)
async def revoke_install_token(
    _auth: BSVibeUser = Depends(require_permission(Permission.admin_settings)),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> None:
    """Revoke this tenant's install token."""
    tenant = await _get_tenant(db, tenant_id)
    tenant.worker_install_token_hash = None
    await db.commit()


async def resolve_install_token_tenant(token: str, db: AsyncSession) -> uuid.UUID | None:
    """Find the tenant a worker install token belongs to.

    Returns ``None`` when no tenant has minted this token. The worker
    register endpoint treats that as "invalid token" instead of falling
    back to the default tenant — multi-tenant deployments must mint
    explicit tokens.
    """
    token_hash = _hash_install_token(token)
    result = await db.execute(
        select(models.Tenant).where(models.Tenant.worker_install_token_hash == token_hash)
    )
    tenant = result.scalar_one_or_none()
    return tenant.id if tenant else None


async def verify_install_token(token: str, db: AsyncSession) -> bool:
    """Backwards-compatible boolean check used by older callers."""
    return await resolve_install_token_tenant(token, db) is not None
