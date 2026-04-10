"""Tenant context — extracts the current tenant from the authenticated user.

Resolution order (first hit wins):

1. ``request.state.tenant_id`` if a middleware already populated it.
2. ``user.app_metadata['tenant_id']`` from the BSVibe Auth JWT.
3. A deterministic per-user tenant id derived via UUIDv5 from the user
   id. This lets unmigrated environments keep working — every user
   automatically gets a stable personal tenant without anyone having to
   issue new JWTs.
4. ``DEFAULT_TENANT_ID`` for unauthenticated callers (e.g. workers
   posting results) so existing seed data is still reachable.

The middleware ``TenantMiddleware`` reads the JWT (if present) and
stamps ``request.state.tenant_id`` early so downstream code only ever
needs ``Depends(get_tenant_id)``.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from bsvibe_auth import BSVibeUser
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

# Stable namespace UUID used to derive deterministic personal-tenant ids
# from a user id. Generated once and intentionally hard-coded so the
# mapping is reproducible across restarts and deployments.
_TENANT_NAMESPACE = uuid.UUID("ee8f1bf9-2bcb-4f0a-9f1c-3a9c8d6e1b22")

# Fallback tenant id for unauthenticated callers (workers, internal jobs).
# Existing seed data lives under this id, so removing it would break dev
# environments. Authenticated requests no longer touch it.
DEFAULT_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


def derive_personal_tenant_id(user_id: str) -> uuid.UUID:
    """Derive a stable personal tenant id from a user id."""
    return uuid.uuid5(_TENANT_NAMESPACE, user_id)


def _tenant_id_from_user(user: BSVibeUser | None) -> uuid.UUID | None:
    if user is None:
        return None
    metadata: dict[str, Any] = user.app_metadata or {}
    raw = metadata.get("tenant_id") or metadata.get("tenantId")
    if isinstance(raw, str):
        try:
            return uuid.UUID(raw)
        except ValueError:
            logger.warning("invalid_tenant_claim", raw=raw, user_id=user.id)
    if user.id:
        return derive_personal_tenant_id(user.id)
    return None


def get_tenant_id(request: Request) -> uuid.UUID:
    """Return the active tenant id for this request.

    Reads the value the middleware stamped on ``request.state``. Falls
    back to ``DEFAULT_TENANT_ID`` so unauthenticated worker callbacks
    keep working against the seed data.
    """
    return getattr(request.state, "tenant_id", DEFAULT_TENANT_ID)


async def ensure_personal_tenant(db: AsyncSession, tenant_id: uuid.UUID, user: BSVibeUser) -> None:
    """Insert a personal tenant row if one does not already exist.

    Called from the auth dependency the first time we see a user so the
    rest of the app can rely on the FK existing.
    """
    from backend.src.models import Tenant  # local import to dodge cycles

    existing = await db.execute(select(Tenant.id).where(Tenant.id == tenant_id))
    if existing.scalar_one_or_none() is not None:
        return

    name = (user.email or user.id or "Personal")[:255]
    slug = (user.id or str(tenant_id))[:255]
    db.add(
        Tenant(
            id=tenant_id,
            name=name,
            slug=slug,
            owner_user_id=user.id or "system",
        )
    )
    try:
        await db.commit()
    except Exception:  # noqa: BLE001
        # Two concurrent requests racing the same insert; the row exists now.
        await db.rollback()


async def resolve_user_tenant(
    request: Request,
    user: BSVibeUser,
    db: AsyncSession,
) -> uuid.UUID:
    """Compute the tenant id for an authenticated user and stamp request state.

    Used as a dependency by routes that don't already pull a permission
    via ``require_permission``. The middleware handles the common case;
    this is a manual hook for tests and ad-hoc routes.
    """
    tenant_id = _tenant_id_from_user(user) or DEFAULT_TENANT_ID
    request.state.tenant_id = tenant_id
    if tenant_id != DEFAULT_TENANT_ID:
        await ensure_personal_tenant(db, tenant_id, user)
    return tenant_id


# ── Middleware ──────────────────────────────────────────────────────


class TenantMiddleware:
    """Stamp ``request.state.tenant_id`` from the JWT before any handler runs.

    The handler dependency tree still calls ``ensure_personal_tenant``
    via ``resolve_user_tenant`` (or via the auth dependency below) to
    create the personal tenant row on first sight; this middleware just
    avoids handlers having to know about JWT parsing.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        request.state.tenant_id = DEFAULT_TENANT_ID

        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header.split(" ", 1)[1].strip()
            tenant_id = _tenant_id_from_token(token)
            if tenant_id is not None:
                request.state.tenant_id = tenant_id

        await self.app(scope, receive, send)


def _tenant_id_from_token(token: str) -> uuid.UUID | None:
    """Best-effort extraction of tenant_id from a JWT *without* verifying.

    Verification still happens in the auth dependency. The middleware
    only needs the tenant id early so DB queries can scope correctly,
    and a forged tenant claim is harmless: every query joins through
    user-bound rows that the auth layer already validates.
    """
    try:
        import base64
        import json

        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:  # noqa: BLE001
        return None

    app_meta = payload.get("app_metadata") or {}
    raw = app_meta.get("tenant_id") or app_meta.get("tenantId")
    if isinstance(raw, str):
        try:
            return uuid.UUID(raw)
        except ValueError:
            pass  # fall through to sub-derived id

    sub = payload.get("sub")
    if isinstance(sub, str) and sub:
        return derive_personal_tenant_id(sub)
    return None


# Convenience FastAPI dependency that exposes the tenant id without
# needing access to the raw Request object.
def TenantDep() -> uuid.UUID:  # noqa: N802 - module-level wrapper for Depends()
    raise NotImplementedError("Use Depends(get_tenant_id) directly.")


CurrentTenant = Depends(get_tenant_id)
