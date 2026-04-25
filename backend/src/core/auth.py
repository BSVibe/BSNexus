"""JWT-based authentication via bsvibe-auth."""

import enum
import os

from bsvibe_auth import BSVibeUser, BsvibeAuthProvider
from bsvibe_auth.errors import AuthError
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.config import settings
from backend.src.storage.database import get_db


def _is_production_environment() -> bool:
    """Return ``True`` if the runtime environment is production.

    Reads from (in order): ``settings.environment``, then the raw
    ``ENVIRONMENT`` env var. The env-var fallback exists so tests and
    operators can flip production-mode without bouncing the process to
    re-read pydantic settings.
    """
    env = (settings.environment or os.getenv("ENVIRONMENT") or "").strip().lower()
    return env == "production"


def _e2e_bypass_enabled() -> bool:
    """``True`` only if the e2e bypass token is set AND we are not in prod.

    The bypass token short-circuits ``BsvibeAuthProvider.verify_token``
    and returns a synthetic admin user — invaluable for local dev and
    Playwright runs, catastrophic if accidentally honored in prod.
    """
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


auth_provider = BsvibeAuthProvider(auth_url=settings.bsvibe_auth_url)


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


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> BSVibeUser:
    """Authenticate the request and upsert the personal tenant row.

    Wraps the bsvibe-auth dependency so every authenticated handler is
    guaranteed a Tenant row exists for the user's tenant_id before any
    FK insert (projects, requests, deliverables, ...) runs. Without
    this, brand-new users hit ``ForeignKeyViolationError`` on their
    first mutating call.

    SECURITY:
      * The JWT signature is verified by
        ``BsvibeAuthProvider.verify_token`` — a forged token raises
        ``AuthError`` and we surface 401.
      * The verified user's tenant id is re-stamped onto
        ``request.state.tenant_id``, overriding any unverified value
        a middleware may have written. ``get_tenant_id`` reads from
        that state, so handlers always see the post-verification tenant.
      * The ``E2E_TEST_TOKEN`` bypass is only honored when
        ``ENVIRONMENT`` is non-production (see ``_e2e_bypass_enabled``).
        A leaked dev bypass token is inert in prod.
    """
    # Local import to avoid circular dependency between auth and tenant_context.
    from backend.src.core.tenant_context import (
        DEFAULT_TENANT_ID,
        _tenant_id_from_user,
        ensure_personal_tenant,
    )

    # Token resolution order:
    #   1. ``Authorization: Bearer <token>`` — preferred
    #   2. ``?token=<token>`` query string — required for SSE endpoints
    #      because the browser EventSource API does not support custom
    #      headers. The query string token is treated identically to a
    #      bearer header — same bypass rules, same JWT verification.
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

    user: BSVibeUser
    if _e2e_bypass_enabled() and raw_token == settings.e2e_test_token:
        user = _build_e2e_test_user()
    else:
        try:
            user = await auth_provider.verify_token(raw_token)
        except AuthError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=exc.message,
                headers={"WWW-Authenticate": "Bearer"},
            )

    tenant_id = _tenant_id_from_user(user)
    if tenant_id is not None and tenant_id != DEFAULT_TENANT_ID:
        await ensure_personal_tenant(db, tenant_id, user)
        # Stamp on request.state so get_tenant_id sees the right value
        # even when the middleware ran with a different/no claim.
        request.state.tenant_id = tenant_id
    else:
        # No usable tenant id — make sure request.state reflects that
        # rather than carrying whatever the middleware seeded.
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
