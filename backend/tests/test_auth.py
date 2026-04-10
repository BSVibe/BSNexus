"""Tests for JWT-based authentication and RBAC permission system."""

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from backend.src.core.auth import (
    Permission,
    Role,
    ROLE_PERMISSIONS,
    require_permission,
)

pytestmark = pytest.mark.asyncio


class TestRolePermissions:
    def test_admin_has_all_permissions(self):
        assert ROLE_PERMISSIONS[Role.admin] == set(Permission)

    def test_viewer_has_read_only(self):
        viewer_perms = ROLE_PERMISSIONS[Role.viewer]
        assert Permission.project_read in viewer_perms
        assert Permission.task_read in viewer_perms
        assert Permission.plan_read in viewer_perms
        assert Permission.project_create not in viewer_perms
        assert Permission.task_create not in viewer_perms
        assert Permission.admin_settings not in viewer_perms

    def test_operator_has_crud_but_not_admin(self):
        op_perms = ROLE_PERMISSIONS[Role.operator]
        assert Permission.project_create in op_perms
        assert Permission.task_create in op_perms
        assert Permission.admin_settings not in op_perms
        assert Permission.admin_security not in op_perms

    def test_worker_has_minimal_permissions(self):
        worker_perms = ROLE_PERMISSIONS[Role.worker]
        assert Permission.task_read in worker_perms
        assert Permission.task_transition in worker_perms
        assert Permission.project_create not in worker_perms

    def test_admin_tokens_permission_removed(self):
        """admin_tokens was removed with API key management."""
        permission_values = [p.value for p in Permission]
        assert "admin.tokens" not in permission_values


class TestRequirePermission:
    async def test_admin_role_grants_any_permission(self):
        """Admin role in app_metadata should pass any permission check."""
        mock_user = AsyncMock()
        mock_user.app_metadata = {"role": "admin"}

        checker = require_permission(Permission.admin_security)
        result = await checker(user=mock_user)
        assert result is mock_user

    async def test_viewer_denied_write_permission(self):
        """Viewer role should be denied write permissions."""
        mock_user = AsyncMock()
        mock_user.app_metadata = {"role": "viewer"}

        checker = require_permission(Permission.project_create)
        with pytest.raises(HTTPException) as exc_info:
            await checker(user=mock_user)
        assert exc_info.value.status_code == 403

    async def test_viewer_allowed_read_permission(self):
        """Viewer role should pass read permission checks."""
        mock_user = AsyncMock()
        mock_user.app_metadata = {"role": "viewer"}

        checker = require_permission(Permission.project_read)
        result = await checker(user=mock_user)
        assert result is mock_user

    async def test_unknown_role_defaults_to_viewer(self):
        """Unknown role in app_metadata should default to viewer permissions."""
        mock_user = AsyncMock()
        mock_user.app_metadata = {"role": "superadmin"}

        # Viewer can read projects
        checker = require_permission(Permission.project_read)
        result = await checker(user=mock_user)
        assert result is mock_user

        # But cannot create
        checker = require_permission(Permission.project_create)
        with pytest.raises(HTTPException) as exc_info:
            await checker(user=mock_user)
        assert exc_info.value.status_code == 403

    async def test_missing_role_defaults_to_viewer(self):
        """Missing role in app_metadata should default to viewer."""
        mock_user = AsyncMock()
        mock_user.app_metadata = {}

        checker = require_permission(Permission.project_read)
        result = await checker(user=mock_user)
        assert result is mock_user

    async def test_worker_role_permissions(self):
        """Worker role should have task_read and task_transition."""
        mock_user = AsyncMock()
        mock_user.app_metadata = {"role": "worker"}

        for perm in [Permission.task_read, Permission.task_transition, Permission.worker_read]:
            checker = require_permission(perm)
            result = await checker(user=mock_user)
            assert result is mock_user

        checker = require_permission(Permission.project_create)
        with pytest.raises(HTTPException) as exc_info:
            await checker(user=mock_user)
        assert exc_info.value.status_code == 403

    async def test_operator_role_permissions(self):
        """Operator role should have CRUD but not admin permissions."""
        mock_user = AsyncMock()
        mock_user.app_metadata = {"role": "operator"}

        checker = require_permission(Permission.project_create)
        result = await checker(user=mock_user)
        assert result is mock_user

        checker = require_permission(Permission.admin_security)
        with pytest.raises(HTTPException) as exc_info:
            await checker(user=mock_user)
        assert exc_info.value.status_code == 403
