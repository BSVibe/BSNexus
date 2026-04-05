"""Tenant context — extracts tenant from request for multi-tenancy filtering."""

from __future__ import annotations

import uuid

import structlog
from fastapi import Request

logger = structlog.get_logger(__name__)

# Default tenant for single-tenant deployments and backward compatibility
DEFAULT_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


def get_tenant_id(request: Request) -> uuid.UUID:
    """Extract tenant_id from request state (set by middleware) or return default.

    In the future, this will be populated by middleware reading from:
    1. JWT claims (BSVibe Auth)
    2. X-API-Key header → tenant lookup
    3. Subdomain-based routing

    For now, returns the default tenant for backward compatibility.
    """
    return getattr(request.state, "tenant_id", DEFAULT_TENANT_ID)
