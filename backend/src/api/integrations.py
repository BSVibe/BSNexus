"""Integrations API — per-tenant BSage + BSupervisor config.

BSGateway isn't an integration anymore — it's an executor kind.
Register it via the Executors tab (``executor_type="bsgateway"``).

Endpoints:
- GET    /api/v1/integrations                 — list all three as safe view
- PATCH  /api/v1/integrations/{provider}      — upsert config for one provider
- POST   /api/v1/integrations/{provider}/test — ping reachable + auth check
"""

from __future__ import annotations

import uuid

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.config import settings as app_settings
from backend.src.core.auth import get_current_user
from backend.src.core.encryption import EncryptionManager
from backend.src.core.integrations import invalidate_tenant_cache
from backend.src.core.tenant_context import get_tenant_id
from backend.src.models.tenant_integration_config import (
    IntegrationProvider,
    TenantIntegrationConfig,
)
from backend.src.schemas import (
    IntegrationConfigList,
    IntegrationConfigResponse,
    IntegrationConfigUpdate,
    IntegrationTestResult,
    redacted,
)
from backend.src.storage.database import get_db

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/integrations", tags=["integrations"])


def _encryption() -> EncryptionManager:
    return EncryptionManager(app_settings.encryption_key)


def _parse_provider(raw: str) -> IntegrationProvider:
    try:
        return IntegrationProvider(raw)
    except ValueError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Unknown provider '{raw}'. Expected one of: "
            f"{', '.join(p.value for p in IntegrationProvider)}",
        )


async def _load_row(
    db: AsyncSession, tenant_id: uuid.UUID, provider: IntegrationProvider
) -> TenantIntegrationConfig | None:
    stmt = select(TenantIntegrationConfig).where(
        TenantIntegrationConfig.tenant_id == tenant_id,
        TenantIntegrationConfig.provider == provider,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


@router.get("", response_model=IntegrationConfigList)
async def list_integrations(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
) -> IntegrationConfigList:
    rows = {
        row.provider: row
        for row in (
            await db.execute(
                select(TenantIntegrationConfig).where(
                    TenantIntegrationConfig.tenant_id == tenant_id
                )
            )
        ).scalars()
    }
    return IntegrationConfigList(
        bsage=redacted(IntegrationProvider.bsage, rows.get(IntegrationProvider.bsage)),
        bsupervisor=redacted(
            IntegrationProvider.bsupervisor, rows.get(IntegrationProvider.bsupervisor)
        ),
    )


@router.patch("/{provider}", response_model=IntegrationConfigResponse)
async def update_integration(
    provider: str,
    payload: IntegrationConfigUpdate,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
) -> IntegrationConfigResponse:
    prov = _parse_provider(provider)
    row = await _load_row(db, tenant_id, prov)

    if row is None:
        row = TenantIntegrationConfig(tenant_id=tenant_id, provider=prov)
        db.add(row)

    updates = payload.model_dump(exclude_unset=True)

    if "enabled" in updates:
        row.enabled = bool(updates["enabled"])
    if "base_url" in updates:
        row.base_url = updates["base_url"] or None
    if "extra_config" in updates:
        row.extra_config = updates["extra_config"] or {}
    if "api_key" in updates:
        if updates["api_key"] is None:
            row.api_key_encrypted = None
        elif updates["api_key"] == "":
            row.api_key_encrypted = None
        else:
            row.api_key_encrypted = _encryption().encrypt_value(updates["api_key"])

    await db.commit()
    await db.refresh(row)
    invalidate_tenant_cache(tenant_id)
    logger.info(
        "integration_updated",
        tenant_id=str(tenant_id),
        provider=prov.value,
        enabled=row.enabled,
    )
    return redacted(prov, row)


@router.post("/{provider}/test", response_model=IntegrationTestResult)
async def test_integration(
    provider: str,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
) -> IntegrationTestResult:
    prov = _parse_provider(provider)
    row = await _load_row(db, tenant_id, prov)

    if row is None or not row.enabled or not row.base_url:
        return IntegrationTestResult(
            ok=False, status="disabled", detail="Enable + set base URL first"
        )

    probe_path = _probe_path_for(prov)
    # Explicit service UA to clear Cloudflare Bot Fight Mode on the
    # *.bsvibe.dev frontends (httpx's default python-httpx UA gets 403'd).
    headers: dict[str, str] = {
        "User-Agent": "BSNexus/0.2 (+https://nexus.bsvibe.dev)",
    }
    if row.api_key_encrypted:
        try:
            token = _encryption().decrypt_value(row.api_key_encrypted)
            headers["Authorization"] = f"Bearer {token}"
        except ValueError:
            logger.error(
                "integration_api_key_decrypt_failed", provider=prov.value
            )

    url = f"{row.base_url.rstrip('/')}{probe_path}"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(url, headers=headers)
    except httpx.TimeoutException:
        return IntegrationTestResult(
            ok=False, status="unreachable", detail="timeout"
        )
    except Exception as exc:  # noqa: BLE001
        return IntegrationTestResult(
            ok=False, status="unreachable", detail=str(exc)
        )

    if resp.status_code in (401, 403):
        return IntegrationTestResult(
            ok=False, status="unauthorized", detail=f"HTTP {resp.status_code}"
        )
    if resp.status_code < 500:
        return IntegrationTestResult(ok=True, status="healthy")
    return IntegrationTestResult(
        ok=False, status="unreachable", detail=f"HTTP {resp.status_code}"
    )


def _probe_path_for(provider: IntegrationProvider) -> str:
    return {
        IntegrationProvider.bsage: "/api/health",
        IntegrationProvider.bsupervisor: "/api/health",
    }[provider]
