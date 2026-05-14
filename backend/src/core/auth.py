"""Auth dispatch via ``bsvibe-authz``.

Delegates token verification to :func:`bsvibe_authz.deps.get_current_user`,
which performs the canonical opaque → JWT → PAT-JWT introspection-fallback
flow. Future bsvibe-authz auth changes propagate to BSNexus automatically
— same shape as BSage's ``combined_principal``.

The pre-existing ``E2E_TEST_TOKEN`` bypass short-circuits BEFORE
dispatch in non-production environments. It is the only path that
returns a synthetic ``admin@bsvibe.dev``-style user; production never
honors it (see :func:`_e2e_bypass_enabled`).
"""

from __future__ import annotations

import enum
import os
from typing import Any, cast

from bsvibe_authz import (
    IntrospectionCache,
    IntrospectionClient,
    Settings as AuthzSettings,
    User as AuthzUser,
)
from bsvibe_authz.deps import get_current_user as bsvibe_authz_get_current_user
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.config import settings
from backend.src.core.tenant_context import (
    DEFAULT_TENANT_ID,
    BSVibeUser,
    _tenant_id_from_user,
    ensure_personal_tenant,
)
from backend.src.storage.database import get_db

OPAQUE_TOKEN_PREFIX = "bsv_sk_"


def _is_production_environment() -> bool:
    """Return ``True`` if the runtime environment is production."""
    env = (os.getenv("ENVIRONMENT") or settings.environment or "").strip().lower()
    return env == "production"


def _e2e_bypass_enabled() -> bool:
    """``True`` only if the e2e bypass token is set AND we are not in prod."""
    if _is_production_environment():
        return False
    return bool(settings.e2e_test_token)


class Role(str, enum.Enum):
    admin = "admin"
    operator = "operator"
    viewer = "viewer"
    worker = "worker"


class Permission(str, enum.Enum):
    # Project permissions
    project_create = "project.create"
    project_read = "project.read"
    project_update = "project.update"
    project_delete = "project.delete"

    # Task permissions
    task_create = "task.create"
    task_read = "task.read"
    task_update = "task.update"
    task_delete = "task.delete"
    task_transition = "task.transition"

    # Worker permissions
    worker_register = "worker.register"
    worker_read = "worker.read"
    worker_manage = "worker.manage"

    # Admin permissions
    admin_settings = "admin.settings"
    admin_audit = "admin.audit"
    admin_security = "admin.security"

    # Plan view permissions
    plan_read = "plan.read"

    # Planner permissions
    planner_read = "planner.read"
    planner_manage = "planner.manage"


# Role-to-permissions mapping
ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.admin: set(Permission),  # All permissions
    Role.operator: {
        Permission.project_create,
        Permission.project_read,
        Permission.project_update,
        Permission.task_create,
        Permission.task_read,
        Permission.task_update,
        Permission.task_transition,
        Permission.worker_register,
        Permission.worker_read,
        Permission.worker_manage,
        Permission.plan_read,
        Permission.planner_read,
        Permission.planner_manage,
    },
    Role.viewer: {
        Permission.project_read,
        Permission.task_read,
        Permission.worker_read,
        Permission.plan_read,
        Permission.planner_read,
    },
    Role.worker: {
        Permission.task_read,
        Permission.task_transition,
        Permission.worker_read,
    },
}


def _authz_settings() -> AuthzSettings:
    """Build a :class:`bsvibe_authz.Settings` instance from BSNexus env.

    Constructed per-call so test patches against ``backend.src.config.settings``
    take effect without restarting the process. The OpenFGA fields are
    placeholders — the dispatch never calls OpenFGA from BSNexus today
    (RBAC still runs through :class:`Permission` / :func:`require_permission`).
    """
    user_jwt_secret = os.getenv("USER_JWT_SECRET")
    user_jwt_public_key = os.getenv("USER_JWT_PUBLIC_KEY")
    user_jwt_algorithm = cast(Any, os.getenv("USER_JWT_ALGORITHM", "HS256"))
    return AuthzSettings(
        bsvibe_auth_url=settings.bsvibe_auth_url,
        openfga_api_url="",
        openfga_store_id="",
        openfga_auth_model_id="",
        service_token_signing_secret=settings.service_token_signing_secret or "",
        user_jwt_secret=user_jwt_secret,
        user_jwt_public_key=user_jwt_public_key,
        user_jwt_algorithm=user_jwt_algorithm,
        user_jwt_audience=os.getenv("USER_JWT_AUDIENCE", "authenticated"),
        user_jwt_issuer=os.getenv("USER_JWT_ISSUER"),
        # Phase 8 §사전 발견 #6 — adopt JWKS URL so verify_user_jwt picks
        # up Supabase ES256 key rotation without a redeploy. Empty
        # default leaves the legacy secret/public-key paths intact.
        user_jwt_jwks_url=settings.user_jwt_jwks_url or None,
        introspection_url=settings.introspection_url,
        introspection_client_id=settings.introspection_client_id,
        introspection_client_secret=settings.introspection_client_secret,
    )


_introspection_client: IntrospectionClient | None = None
_introspection_cache: IntrospectionCache | None = None


def _get_introspection_client() -> IntrospectionClient | None:
    """Lazy-build the RFC 7662 introspection client. ``None`` when disabled."""
    global _introspection_client
    if _introspection_client is not None:
        return _introspection_client
    if not settings.introspection_url:
        return None
    _introspection_client = IntrospectionClient(
        introspection_url=settings.introspection_url,
        client_id=settings.introspection_client_id,
        client_secret=settings.introspection_client_secret,
    )
    return _introspection_client


def _get_introspection_cache() -> IntrospectionCache:
    global _introspection_cache
    if _introspection_cache is None:
        _introspection_cache = IntrospectionCache(ttl_s=30)
    return _introspection_cache


def _build_e2e_test_user() -> BSVibeUser:
    """Synthesize a BSVibeUser for the e2e bypass token path."""
    return BSVibeUser(
        id=settings.e2e_test_user_id,
        email=settings.e2e_test_user_email,
        app_metadata={
            "tenant_id": settings.e2e_test_user_tenant_id,
            "role": "admin",
        },
        user_metadata={},
    )


def _to_bsvibe_user(authz_user: AuthzUser, *, default_role: str = "viewer") -> BSVibeUser:
    """Translate a ``bsvibe_authz.User`` into the BSVibeUser shape BSNexus
    consumes (``app_metadata['role']`` + ``app_metadata['tenant_id']``).

    Trust order for ``app_metadata``:

    1. ``authz_user.app_metadata`` — populated by the lib's
       :func:`parse_user_token` from the verified user-JWT payload. For
       Supabase admins this carries ``role: "admin"``.
    2. Empty (opaque + PAT-JWT-introspection paths). Fall back to
       ``"admin"`` for ``is_service`` principals, otherwise
       ``default_role``.

    Tenant id falls back from ``authz_user.active_tenant_id`` (which the
    lib already lifts from ``app_metadata.tenant_id`` when the
    top-level claim is absent).
    """
    raw_meta = dict(authz_user.app_metadata) if authz_user.app_metadata else {}

    role = raw_meta.get("role")
    if not isinstance(role, str) or not role:
        role = "admin" if authz_user.is_service else default_role
    raw_meta["role"] = role

    if authz_user.active_tenant_id and not raw_meta.get("tenant_id"):
        raw_meta["tenant_id"] = authz_user.active_tenant_id

    return BSVibeUser(
        id=authz_user.id,
        email=authz_user.email,
        app_metadata=raw_meta,
        user_metadata=dict(authz_user.user_metadata) if authz_user.user_metadata else {},
    )


async def _dispatch_token(token: str) -> BSVibeUser:
    """Run the bsvibe-authz dispatch and return a BSVibeUser.

    Delegates to :func:`bsvibe_authz.deps.get_current_user` for the full
    opaque → JWT → PAT-JWT-introspection flow, then maps the returned
    :class:`bsvibe_authz.User` to BSNexus's :class:`BSVibeUser` shape.
    Library-level dispatch changes propagate here automatically.

    Since bsvibe-authz #22 the lib's ``parse_user_token`` lifts
    ``app_metadata`` / ``user_metadata`` off the verified JWT payload and
    falls back ``active_tenant_id`` to ``app_metadata.tenant_id`` —
    BSNexus no longer re-decodes the token. ``_to_bsvibe_user`` is the
    sole adapter.
    """
    try:
        authz_user = await bsvibe_authz_get_current_user(
            authorization=f"Bearer {token}",
            settings=_authz_settings(),
            introspection_client=_get_introspection_client(),
            introspection_cache=_get_introspection_cache(),
        )
    except HTTPException as exc:
        # RFC 6750 §3 requires ``WWW-Authenticate: Bearer`` on 401 —
        # re-raise with the header attached when the lib didn't.
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            existing = {k.lower() for k in (exc.headers or {})}
            if "www-authenticate" not in existing:
                raise HTTPException(
                    status_code=exc.status_code,
                    detail=exc.detail,
                    headers={"WWW-Authenticate": "Bearer"},
                ) from exc
        raise

    return _to_bsvibe_user(authz_user)


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> BSVibeUser:
    """Authenticate the request and upsert the personal tenant row.

    Token resolution order:

      1. ``Authorization: Bearer <token>`` — preferred.
      2. ``?token=<token>`` query string — required for SSE endpoints
         because the browser EventSource API does not support custom
         headers. Treated identically to the bearer header.

    SECURITY:

      * Verification runs through ``bsvibe-authz`` (opaque → JWT). A
        forged token surfaces ``HTTP 401``.
      * The verified user's tenant id is re-stamped onto
        ``request.state.tenant_id``, overriding any unverified value the
        middleware may have written. ``get_tenant_id`` reads from that
        state, so handlers always see the post-verification tenant.
      * The ``E2E_TEST_TOKEN`` bypass is only honored when ``ENVIRONMENT``
        is non-production (see :func:`_e2e_bypass_enabled`). A leaked
        dev bypass token is inert in prod.
    """
    raw_token = ""
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        raw_token = auth_header.split(" ", 1)[1].strip()
    elif "token" in request.query_params:
        raw_token = (request.query_params.get("token") or "").strip()

    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if _e2e_bypass_enabled() and raw_token == settings.e2e_test_token:
        user = _build_e2e_test_user()
    else:
        user = await _dispatch_token(raw_token)

    tenant_id = _tenant_id_from_user(user)
    if tenant_id is not None and tenant_id != DEFAULT_TENANT_ID:
        await ensure_personal_tenant(db, tenant_id, user)
        request.state.tenant_id = tenant_id
    else:
        request.state.tenant_id = DEFAULT_TENANT_ID

    return user


def require_permission(permission: Permission):
    """FastAPI dependency that checks JWT auth + RBAC permission.

    Usage:
        @router.get("/admin/settings")
        async def get_settings(
            user: BSVibeUser = Depends(require_permission(Permission.admin_settings)),
        ):
            ...
    """

    async def _check_permission(
        user: BSVibeUser = Depends(get_current_user),
    ) -> BSVibeUser:
        role_str = user.app_metadata.get("role", "viewer")
        try:
            role = Role(role_str)
        except ValueError:
            role = Role.viewer

        role_perms = ROLE_PERMISSIONS.get(role, set())
        if permission not in role_perms:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required: {permission.value}",
            )
        return user

    return _check_permission
