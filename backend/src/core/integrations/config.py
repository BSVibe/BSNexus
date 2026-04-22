"""Tenant integration config — load + decrypt provider rows.

The three sibling services (BSage, BSGateway, BSupervisor) each have a
row in ``tenant_integration_configs`` per tenant. Rows are lazily
created on first write from the Settings UI.

``get_tenant_integration_snapshot`` caches per tenant for a short TTL
(60s) to avoid hitting the DB on every run dispatch.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.config import settings
from backend.src.core.encryption import EncryptionManager
from backend.src.models.tenant_integration_config import (
    IntegrationProvider,
    TenantIntegrationConfig,
)

logger = structlog.get_logger(__name__)

_CACHE_TTL_SECONDS = 60.0
_cache: dict[uuid.UUID, tuple[float, "TenantIntegrationSnapshot"]] = {}


@dataclass(frozen=True)
class ProviderConfig:
    """Runtime view of one integration provider for one tenant."""

    enabled: bool
    base_url: str | None = None
    api_key: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AuditProviderConfig(ProviderConfig):
    """BSupervisor-specific config with timeout/fail-mode defaults."""

    @property
    def timeout_ms(self) -> int:
        value = self.extra.get("timeout_ms", 200)
        return int(value) if value is not None else 200

    @property
    def fail_mode(self) -> str:
        """'open' (allow on timeout, default) or 'closed' (block on timeout)."""
        return str(self.extra.get("fail_mode", "open"))


@dataclass(frozen=True)
class TenantIntegrationSnapshot:
    """All three integration configs resolved for one tenant.

    Disabled or missing providers are represented as None so callers
    fall back to Noop implementations.
    """

    tenant_id: uuid.UUID
    bsage: ProviderConfig | None
    bsgateway: ProviderConfig | None
    bsupervisor: AuditProviderConfig | None


def _decrypt(value: str | None) -> str | None:
    if not value:
        return None
    try:
        mgr = EncryptionManager(settings.encryption_key)
        return mgr.decrypt_value(value)
    except ValueError:
        logger.error("integration_api_key_decrypt_failed")
        return None


def _row_to_provider(
    row: TenantIntegrationConfig | None,
) -> ProviderConfig | AuditProviderConfig | None:
    if row is None or not row.enabled:
        return None

    cls = (
        AuditProviderConfig
        if row.provider == IntegrationProvider.bsupervisor
        else ProviderConfig
    )
    return cls(
        enabled=True,
        base_url=row.base_url,
        api_key=_decrypt(row.api_key_encrypted),
        extra=dict(row.extra_config or {}),
    )


async def _load_rows(
    db: AsyncSession, tenant_id: uuid.UUID
) -> dict[IntegrationProvider, TenantIntegrationConfig]:
    stmt = select(TenantIntegrationConfig).where(
        TenantIntegrationConfig.tenant_id == tenant_id
    )
    result = await db.execute(stmt)
    return {row.provider: row for row in result.scalars()}


async def get_tenant_integration_snapshot(
    db: AsyncSession, tenant_id: uuid.UUID, *, use_cache: bool = True
) -> TenantIntegrationSnapshot:
    """Resolve a snapshot of all three provider configs for one tenant.

    Results are cached for ``_CACHE_TTL_SECONDS``. Pass ``use_cache=False``
    to bypass (e.g. right after a settings write).
    """
    now = time.monotonic()
    cached = _cache.get(tenant_id) if use_cache else None
    if cached and (now - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]

    rows = await _load_rows(db, tenant_id)
    snapshot = TenantIntegrationSnapshot(
        tenant_id=tenant_id,
        bsage=_row_to_provider(rows.get(IntegrationProvider.bsage)),
        bsgateway=_row_to_provider(rows.get(IntegrationProvider.bsgateway)),
        bsupervisor=_row_to_provider(rows.get(IntegrationProvider.bsupervisor)),
    )
    _cache[tenant_id] = (now, snapshot)
    return snapshot


def invalidate_tenant_cache(tenant_id: uuid.UUID) -> None:
    """Drop the cached snapshot for a tenant (call after settings change)."""
    _cache.pop(tenant_id, None)
