"""Tenant-scoped integration configuration + provider resolution.

The three sibling services (BSage, BSGateway, BSupervisor) are optional.
Each tenant configures which ones are enabled and their credentials via
the Settings → Integrations UI. Provider factories resolve per-request
against the tenant's config; no global singletons.
"""

from backend.src.core.integrations.config import (
    AuditProviderConfig,
    ProviderConfig,
    TenantIntegrationSnapshot,
    get_tenant_integration_snapshot,
    invalidate_tenant_cache,
)

__all__ = [
    "AuditProviderConfig",
    "ProviderConfig",
    "TenantIntegrationSnapshot",
    "get_tenant_integration_snapshot",
    "invalidate_tenant_cache",
]
