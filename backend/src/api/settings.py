from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src import models, schemas
from backend.src.storage.database import get_db

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])

_LLM_SETTING_KEYS = ("llm_api_key", "llm_model", "llm_base_url")


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
async def get_settings(db: AsyncSession = Depends(get_db)) -> schemas.GlobalSettingsResponse:
    """Return global LLM settings with masked API key."""
    result = await db.execute(select(models.Setting))
    settings_map: dict[str, str] = {s.key: s.value for s in result.scalars().all()}

    return schemas.GlobalSettingsResponse(
        llm_api_key=mask_api_key(settings_map.get("llm_api_key")),
        llm_model=settings_map.get("llm_model"),
        llm_base_url=settings_map.get("llm_base_url"),
    )


@router.put("", response_model=schemas.GlobalSettingsResponse)
async def update_settings(
    body: schemas.GlobalSettingsUpdate,
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
    return await get_settings(db)
