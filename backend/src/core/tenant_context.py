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
    """Upsert the personal tenant row for an authenticated user.

    Account/tenant identity is owned by ``auth.bsvibe.dev`` — our local
    ``tenants`` table is a derived projection of whatever the JWT claims
    say. Every authenticated request runs this so:

      * a brand-new user gets their row inserted on first call
      * a user whose email or display name changed in bsvibe gets the
        local row refreshed
      * concurrent first-touch requests do not race (ON CONFLICT handles
        the duplicate-key case atomically)

    On PostgreSQL we use ``INSERT ... ON CONFLICT (id) DO UPDATE`` so the
    upsert is a single statement. On SQLite (used by some unit tests) we
    fall back to a SELECT + INSERT/UPDATE pair since SQLite needs the
    sqlite-specific ``insert`` and not all driver versions support it.
    """
    from backend.src.models import Tenant  # local import to dodge cycles

    name = (user.email or user.id or "Personal")[:255]
    slug = (user.id or str(tenant_id))[:255]
    owner = user.id or "system"

    dialect = db.bind.dialect.name if db.bind is not None else ""
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = (
            pg_insert(Tenant)
            .values(id=tenant_id, name=name, slug=slug, owner_user_id=owner)
            .on_conflict_do_update(
                index_elements=["id"],
                set_={"name": name, "owner_user_id": owner},
            )
        )
        await db.execute(stmt)
        await db.commit()
        return

    # Dialect-agnostic fallback (SQLite tests, etc.)
    existing = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    row = existing.scalar_one_or_none()
    if row is None:
        db.add(Tenant(id=tenant_id, name=name, slug=slug, owner_user_id=owner))
    else:
        row.name = name
        row.owner_user_id = owner
    try:
        await db.commit()
    except Exception:  # noqa: BLE001
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
            user_stub, tenant_id = _identify_from_token(token)
            if tenant_id is not None and tenant_id != DEFAULT_TENANT_ID:
                request.state.tenant_id = tenant_id
                # Upsert the Tenant row so subsequent FK inserts
                # (projects, requests, ...) just work. Account/tenant
                # identity lives in bsvibe.dev — our local row is a
                # derived projection of whatever the JWT (or e2e bypass
                # token) claims.
                if user_stub is not None:
                    await _upsert_tenant_for_request(tenant_id, user_stub)

        await self.app(scope, receive, send)


async def _upsert_tenant_for_request(tenant_id: uuid.UUID, user: BSVibeUser) -> None:
    """Open a short-lived DB session and run the tenant upsert."""
    # Local imports dodge the import cycle between this module and storage.
    from backend.src.storage.database import async_session

    try:
        async with async_session() as session:
            await ensure_personal_tenant(session, tenant_id, user)
    except Exception as exc:  # noqa: BLE001
        logger.warning("tenant_upsert_middleware_failed", error=str(exc))


def _identify_from_token(token: str) -> tuple[BSVibeUser | None, uuid.UUID | None]:
    """Return ``(user_stub, tenant_id)`` for the supplied bearer token.

    Recognises two token shapes:

    1. The e2e bypass token from ``settings.e2e_test_token`` — only when
       that env var is non-empty. Returns the synthetic test user.
    2. A JWT — payload is decoded *without* signature verification, just
       to extract the tenant id and email/sub. Real signature
       verification still happens in ``get_current_user`` so a forged
       token cannot bypass authorization.
    """
    from backend.src.config import settings

    bypass_token = settings.e2e_test_token
    if bypass_token and token == bypass_token:
        try:
            tid = uuid.UUID(settings.e2e_test_user_tenant_id)
        except ValueError:
            return None, None
        stub = BSVibeUser(
            id=settings.e2e_test_user_id,
            email=settings.e2e_test_user_email,
            app_metadata={"tenant_id": str(tid), "role": "admin"},
            user_metadata={},
        )
        return stub, tid

    payload = _decode_jwt_payload(token)
    if payload is None:
        return None, None

    tenant_id = _tenant_id_from_payload(payload)
    if tenant_id is None:
        return None, None

    sub = payload.get("sub")
    email = payload.get("email")
    role = (payload.get("app_metadata") or {}).get("role", "viewer")
    stub = BSVibeUser(
        id=str(sub) if sub else "unknown",
        email=str(email) if email else None,
        app_metadata={"tenant_id": str(tenant_id), "role": role},
        user_metadata={},
    )
    return stub, tenant_id


def _decode_jwt_payload(token: str) -> dict[str, Any] | None:
    try:
        import base64
        import json

        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:  # noqa: BLE001
        return None


def _tenant_id_from_payload(payload: dict[str, Any]) -> uuid.UUID | None:
    app_meta = payload.get("app_metadata") or {}
    raw = app_meta.get("tenant_id") or app_meta.get("tenantId")
    if isinstance(raw, str):
        try:
            return uuid.UUID(raw)
        except ValueError:
            pass
    sub = payload.get("sub")
    if isinstance(sub, str) and sub:
        return derive_personal_tenant_id(sub)
    return None


# Convenience FastAPI dependency that exposes the tenant id without
# needing access to the raw Request object.
def TenantDep() -> uuid.UUID:  # noqa: N802 - module-level wrapper for Depends()
    raise NotImplementedError("Use Depends(get_tenant_id) directly.")


CurrentTenant = Depends(get_tenant_id)
