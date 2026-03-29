"""JWT-based authentication via bsvibe-auth."""

import enum

import structlog

from bsvibe_auth import BSVibeUser, BsvibeAuthProvider
from bsvibe_auth.fastapi import create_auth_dependency
from fastapi import Depends, HTTPException, status

from backend.src.config import settings

logger = structlog.get_logger(__name__)


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


auth_provider = BsvibeAuthProvider(auth_url=settings.bsvibe_auth_url)
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
