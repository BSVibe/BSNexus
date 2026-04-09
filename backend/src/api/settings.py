from __future__ import annotations

import hashlib
import secrets

from bsvibe_auth import BSVibeUser
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src import models, schemas
from backend.src.core.auth import Permission, require_permission
from backend.src.storage.database import get_db

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])

_LLM_SETTING_KEYS = ("llm_api_key", "llm_model", "llm_base_url", "default_executor_type")
_INSTALL_TOKEN_KEY = "worker_install_token_hash"


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
        default_executor_type=settings_map.get("default_executor_type", "claude_api"),
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


@router.get("/install-token", response_model=InstallTokenResponse)
async def get_install_token(
    _auth: BSVibeUser = Depends(require_permission(Permission.admin_settings)),
    db: AsyncSession = Depends(get_db),
) -> InstallTokenResponse:
    """Check if an install token exists (never returns plaintext)."""
    result = await db.execute(select(models.Setting).where(models.Setting.key == _INSTALL_TOKEN_KEY))
    setting = result.scalar_one_or_none()
    return InstallTokenResponse(has_token=setting is not None)


@router.post("/install-token", response_model=InstallTokenResponse)
async def create_install_token(
    _auth: BSVibeUser = Depends(require_permission(Permission.admin_settings)),
    db: AsyncSession = Depends(get_db),
) -> InstallTokenResponse:
    """Generate a new install token (replaces existing). Returns plaintext once."""
    token = f"bsn-{secrets.token_urlsafe(32)}"
    token_hash = _hash_install_token(token)

    result = await db.execute(select(models.Setting).where(models.Setting.key == _INSTALL_TOKEN_KEY))
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = token_hash
    else:
        db.add(models.Setting(key=_INSTALL_TOKEN_KEY, value=token_hash))
    await db.commit()

    return InstallTokenResponse(token=token, has_token=True)


@router.delete("/install-token", status_code=204)
async def revoke_install_token(
    _auth: BSVibeUser = Depends(require_permission(Permission.admin_settings)),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Revoke the install token."""
    result = await db.execute(select(models.Setting).where(models.Setting.key == _INSTALL_TOKEN_KEY))
    setting = result.scalar_one_or_none()
    if setting:
        await db.delete(setting)
        await db.commit()


async def verify_install_token(token: str, db: AsyncSession) -> bool:
    """Verify an install token against the stored hash."""
    result = await db.execute(select(models.Setting).where(models.Setting.key == _INSTALL_TOKEN_KEY))
    setting = result.scalar_one_or_none()
    if not setting:
        return True  # No token configured = open registration (dev mode)
    return setting.value == _hash_install_token(token)
