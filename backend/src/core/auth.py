"""JWT-based authentication via bsvibe-auth (Supabase)."""

import enum

import httpx
from jwt import PyJWK

from bsvibe_auth import BSVibeUser, SupabaseAuthProvider
from bsvibe_auth.fastapi import create_auth_dependency
from fastapi import Depends, HTTPException, status

from backend.src.config import settings


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

    # Architect permissions
    architect_session = "architect.session"
    architect_finalize = "architect.finalize"

    # Board permissions
    board_read = "board.read"

    # PM permissions
    pm_control = "pm.control"

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
        Permission.architect_session,
        Permission.architect_finalize,
        Permission.board_read,
        Permission.pm_control,
        Permission.planner_read,
        Permission.planner_manage,
    },
    Role.viewer: {
        Permission.project_read,
        Permission.task_read,
        Permission.worker_read,
        Permission.board_read,
        Permission.planner_read,
    },
    Role.worker: {
        Permission.task_read,
        Permission.task_transition,
        Permission.worker_read,
    },
}


class _DevMockUser(BSVibeUser):
    """Mock user for local development when no Supabase is configured."""

    def __init__(self) -> None:
        super().__init__(
            id="dev-user-00000000",
            email="dev@localhost",
            role="authenticated",
            app_metadata={"role": "admin"},
            user_metadata={},
        )


def _build_auth_provider() -> SupabaseAuthProvider:
    """Build SupabaseAuthProvider with the correct key/algorithm.

    Supabase projects may use HS256 (HMAC + jwt_secret) or ES256 (ECDSA + JWKS).
    When supabase_url is set, fetch the JWKS to detect the algorithm automatically.
    """
    if settings.supabase_url:
        try:
            jwks_url = f"{settings.supabase_url}/auth/v1/.well-known/jwks.json"
            resp = httpx.get(jwks_url, timeout=5.0)
            resp.raise_for_status()
            jwks_data = resp.json()
            keys = jwks_data.get("keys", [])
            if keys:
                jwk_obj = PyJWK(keys[0])
                alg = keys[0].get("alg", "ES256")
                return SupabaseAuthProvider(
                    jwt_secret=jwk_obj.key,
                    algorithms=[alg],
                )
        except Exception:
            pass  # Fall through to HS256

    return SupabaseAuthProvider(jwt_secret=settings.supabase_jwt_secret)


# Supabase auth provider + FastAPI dependency
# In dev mode (no supabase_url and debug=True), use a mock user
if settings.debug and not settings.supabase_url:

    async def get_current_user() -> BSVibeUser:
        return _DevMockUser()

else:
    auth_provider = _build_auth_provider()
    get_current_user = create_auth_dependency(auth_provider)


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
