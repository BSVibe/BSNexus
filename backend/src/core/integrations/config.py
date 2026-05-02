"""Tenant integration config — load + decrypt provider rows.

The three sibling services (BSage, BSGateway, BSupervisor) each have a
row in ``tenant_integration_configs`` per tenant. Rows are lazily
created on first write from the Settings UI.

``get_tenant_integration_snapshot`` caches per tenant for a short TTL
(60s) to avoid hitting the DB on every run dispatch.
"""

from __future__ import annotations

import asyncio
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

# S1-3 H12: per-tenant async lock dictionary to coalesce thundering-herd
# cache misses. The first concurrent request for a tenant takes the
# lock and runs ``_load_rows``; the rest park on the same lock and find
# a fresh entry in ``_cache`` when they wake up. Different tenants take
# different locks so unrelated dispatch traffic doesn't serialize.
#
# Locks are bound to the running event loop the first time they are
# requested. We avoid constructing ``asyncio.Lock`` at import time so
# this module is safe to import before a loop exists.
_locks: dict[uuid.UUID, asyncio.Lock] = {}


def _get_tenant_lock(tenant_id: uuid.UUID) -> asyncio.Lock:
    """Return (or lazily create) the per-tenant lock.

    Lazy creation here is safe because all callers run inside the same
    event-loop coroutine: a sync ``dict.get`` / ``dict[...] = ...`` pair
    cannot be preempted between operations on a single asyncio loop —
    the GIL plus single-threaded asyncio scheduler guarantee that the
    re-check after a missed lookup observes any concurrent insert.
    """
    lock = _locks.get(tenant_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[tenant_id] = lock
    return lock


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
    """Integration configs resolved for one tenant.

    Disabled or missing providers are represented as None so callers
    fall back to Noop implementations.
    """

    tenant_id: uuid.UUID
    bsage: ProviderConfig | None
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

    cls = AuditProviderConfig if row.provider == IntegrationProvider.bsupervisor else ProviderConfig
    return cls(
        enabled=True,
        base_url=row.base_url,
        api_key=_decrypt(row.api_key_encrypted),
        extra=dict(row.extra_config or {}),
    )


async def _load_rows(db: AsyncSession, tenant_id: uuid.UUID) -> dict[IntegrationProvider, TenantIntegrationConfig]:
    stmt = select(TenantIntegrationConfig).where(TenantIntegrationConfig.tenant_id == tenant_id)
    result = await db.execute(stmt)
    return {row.provider: row for row in result.scalars()}


async def get_tenant_integration_snapshot(
    db: AsyncSession, tenant_id: uuid.UUID, *, use_cache: bool = True
) -> TenantIntegrationSnapshot:
    """Resolve a snapshot of all three provider configs for one tenant.

    Results are cached for ``_CACHE_TTL_SECONDS``. Pass ``use_cache=False``
    to bypass (e.g. right after a settings write).

    S1-3 H12: cache fills are coalesced through a per-tenant
    ``asyncio.Lock`` so a thundering herd of concurrent first-touch
    dispatches only runs ``_load_rows`` (which decrypts each
    integration's API key) once per tenant.
    """
    if use_cache:
        cached = _cache.get(tenant_id)
        if cached and (time.monotonic() - cached[0]) < _CACHE_TTL_SECONDS:
            return cached[1]

    lock = _get_tenant_lock(tenant_id)
    async with lock:
        # Re-check the cache under the lock — a concurrent caller may
        # have populated it while we were waiting.
        if use_cache:
            cached = _cache.get(tenant_id)
            if cached and (time.monotonic() - cached[0]) < _CACHE_TTL_SECONDS:
                return cached[1]

        rows = await _load_rows(db, tenant_id)
        snapshot = TenantIntegrationSnapshot(
            tenant_id=tenant_id,
            bsage=_row_to_provider(rows.get(IntegrationProvider.bsage)),
            bsupervisor=_row_to_provider(rows.get(IntegrationProvider.bsupervisor)),
        )
        _cache[tenant_id] = (time.monotonic(), snapshot)
        return snapshot


def invalidate_tenant_cache(tenant_id: uuid.UUID) -> None:
    """Drop the cached snapshot for a tenant (call after settings change).

    The per-tenant lock is left in place — it'll be re-used on the next
    fill. The lock dict grows with the unique-tenant set, which is
    bounded by the active tenant population for this process.
    """
    _cache.pop(tenant_id, None)
