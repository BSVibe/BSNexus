"""Access control and permission management for fine-grained authorization."""

import enum
import secrets
import uuid
from datetime import datetime, timezone

from backend.src.storage.database import Base
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import Boolean, DateTime, Enum, Index, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column


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
    admin_tokens = "admin.tokens"
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


class APIKey(Base):
    """API key for authenticating admin/operator access."""

    __tablename__ = "api_keys"
    __table_args__ = (
        Index("ix_api_keys_key_hash", "key_hash"),
        Index("ix_api_keys_role", "role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    key_prefix: Mapped[str] = mapped_column(String(10), nullable=False)
    role: Mapped[Role] = mapped_column(Enum(Role), nullable=False, default=Role.viewer)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)


class AccessController:
    """Manages API key authentication and permission checks."""

    API_KEY_PREFIX = "bsn-"

    @staticmethod
    def generate_api_key() -> tuple[str, str]:
        """Generate a new API key. Returns (full_key, key_hash)."""
        import hashlib

        raw_key = secrets.token_hex(24)
        full_key = f"bsn-{raw_key}"
        key_hash = hashlib.sha256(full_key.encode()).hexdigest()
        return full_key, key_hash

    @staticmethod
    def hash_key(key: str) -> str:
        """Hash an API key for storage/lookup."""
        import hashlib

        return hashlib.sha256(key.encode()).hexdigest()

    @staticmethod
    def has_permission(role: Role, permission: Permission) -> bool:
        """Check if a role has a specific permission."""
        role_perms = ROLE_PERMISSIONS.get(role, set())
        return permission in role_perms

    @staticmethod
    def get_permissions(role: Role) -> set[Permission]:
        """Get all permissions for a role."""
        return ROLE_PERMISSIONS.get(role, set())


# FastAPI security scheme
bearer_scheme = HTTPBearer(auto_error=False)


def require_permission(permission: Permission):
    """FastAPI dependency that checks for a specific permission.

    When no API key is provided and the system has no API keys configured,
    access is granted (bootstrap mode). Once API keys exist, authentication
    is enforced.

    Uses FastAPI's DI system to get the DB session, ensuring it works correctly
    with dependency overrides in tests.

    Usage:
        @router.get("/admin/settings")
        async def get_settings(
            _auth: None = Depends(require_permission(Permission.admin_settings)),
            db: AsyncSession = Depends(get_db),
        ):
            ...
    """
    from backend.src.storage.database import get_db

    async def _check_permission(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
        db: AsyncSession = Depends(get_db),
    ) -> None:
        from sqlalchemy import func as sqlfunc
        from sqlalchemy import select

        # Check if any API keys exist (bootstrap mode)
        count_result = await db.execute(select(sqlfunc.count()).select_from(APIKey))
        key_count = count_result.scalar() or 0

        if key_count == 0:
            # Bootstrap mode: no API keys configured, allow all access
            return

        # API keys exist, require authentication
        if not credentials:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )

        key_hash = AccessController.hash_key(credentials.credentials)
        result = await db.execute(
            select(APIKey).where(
                APIKey.key_hash == key_hash,
                APIKey.is_active.is_(True),
            )
        )
        api_key = result.scalar_one_or_none()

        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or inactive API key",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Check expiration
        if api_key.expires_at and api_key.expires_at < datetime.now(timezone.utc):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API key has expired",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Check permission
        if not AccessController.has_permission(api_key.role, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required: {permission.value}",
            )

        # Update last_used_at
        api_key.last_used_at = datetime.now(timezone.utc)
        await db.flush()

    return _check_permission
