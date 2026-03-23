"""JWT-based authentication via bsvibe-auth (Supabase)."""

import enum

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
    },
    Role.viewer: {
        Permission.project_read,
        Permission.task_read,
        Permission.worker_read,
        Permission.board_read,
    },
    Role.worker: {
        Permission.task_read,
        Permission.task_transition,
        Permission.worker_read,
    },
}

# Supabase auth provider + FastAPI dependency
auth_provider = SupabaseAuthProvider(jwt_secret=settings.supabase_jwt_secret)
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
