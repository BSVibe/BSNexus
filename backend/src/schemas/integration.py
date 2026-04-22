"""Per-tenant integration config schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.src.models.tenant_integration_config import IntegrationProvider


class IntegrationConfigResponse(BaseModel):
    """API-safe view: never exposes the raw api_key."""

    provider: IntegrationProvider
    enabled: bool
    base_url: str | None
    has_api_key: bool
    extra_config: dict[str, Any]

    model_config = ConfigDict(extra="forbid")


class IntegrationConfigList(BaseModel):
    bsage: IntegrationConfigResponse
    bsgateway: IntegrationConfigResponse
    bsupervisor: IntegrationConfigResponse


class IntegrationConfigUpdate(BaseModel):
    enabled: bool | None = None
    base_url: str | None = Field(None, max_length=500)
    api_key: str | None = Field(None, max_length=4_096)
    extra_config: dict[str, Any] | None = None

    model_config = ConfigDict(extra="forbid")


class IntegrationTestResult(BaseModel):
    ok: bool
    status: str  # "healthy" | "unreachable" | "unauthorized" | "disabled"
    detail: str | None = None


def redacted(
    provider: IntegrationProvider,
    row: Any | None,
) -> IntegrationConfigResponse:
    """Convert a TenantIntegrationConfig ORM row to the API view."""
    if row is None:
        return IntegrationConfigResponse(
            provider=provider,
            enabled=False,
            base_url=None,
            has_api_key=False,
            extra_config={},
        )
    return IntegrationConfigResponse(
        provider=provider,
        enabled=row.enabled,
        base_url=row.base_url,
        has_api_key=bool(row.api_key_encrypted),
        extra_config=dict(row.extra_config or {}),
    )
