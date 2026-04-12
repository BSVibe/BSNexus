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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
class TestGetCurrentUserTokenSources:
    """Token can come from a Bearer header OR from a ``?token=`` query string.

    The query-string fallback is the only way the browser EventSource API
    can authenticate against the SSE endpoints (it does not support
    custom headers). Both paths must accept the e2e bypass token, and
    both must reject missing-token requests with 401.
    """

    async def test_query_param_token_used_when_no_authorization_header(
        self, monkeypatch
    ):
        from types import SimpleNamespace

        from backend.src.config import settings as live_settings
        from backend.src.core import auth as auth_mod

        bypass = "test-bypass-token-only-for-this-test"
        monkeypatch.setattr(live_settings, "e2e_test_token", bypass)
        monkeypatch.setattr(live_settings, "e2e_test_user_tenant_id", "00000000-0000-0000-0000-000000000000")

        request = SimpleNamespace(
            headers={},
            query_params={"token": bypass},
            state=SimpleNamespace(),
        )

        db = AsyncMock()
        user = await auth_mod.get_current_user(request=request, db=db)
        assert user.id == live_settings.e2e_test_user_id

    async def test_no_token_anywhere_returns_401(self):
        from types import SimpleNamespace

        from backend.src.core import auth as auth_mod

        request = SimpleNamespace(
            headers={},
            query_params={},
            state=SimpleNamespace(),
        )
        db = AsyncMock()
        with pytest.raises(HTTPException) as exc:
            await auth_mod.get_current_user(request=request, db=db)
        assert exc.value.status_code == 401

    async def test_authorization_header_takes_precedence_over_query_param(
        self, monkeypatch
    ):
        """If both are set, the header wins — query string is fallback only."""
        from types import SimpleNamespace

        from backend.src.config import settings as live_settings
        from backend.src.core import auth as auth_mod

        bypass = "header-wins-token"
        monkeypatch.setattr(live_settings, "e2e_test_token", bypass)
        monkeypatch.setattr(live_settings, "e2e_test_user_tenant_id", "00000000-0000-0000-0000-000000000000")

        request = SimpleNamespace(
            headers={"authorization": f"Bearer {bypass}"},
            query_params={"token": "wrong-token-should-be-ignored"},
            state=SimpleNamespace(),
        )

        db = AsyncMock()
        user = await auth_mod.get_current_user(request=request, db=db)
        assert user.id == live_settings.e2e_test_user_id
