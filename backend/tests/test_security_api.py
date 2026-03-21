"""Tests for security API endpoints."""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.api.security import create_api_key, list_api_keys, list_audit_logs, revoke_api_key, run_security_scan
from backend.src.core.access_control import APIKey, Role
from backend.src.core.audit_logger import AuditAction, AuditLog, AuditLogger
from backend.src.schemas import APIKeyCreateRequest

pytestmark = pytest.mark.asyncio


async def test_security_scan_endpoint(client: AsyncClient):
    """Bootstrap mode: no API keys, so access is granted."""
    resp = await client.get("/api/v1/security/audit/scan")
    assert resp.status_code == 200
    data = resp.json()
    assert "scan_timestamp" in data
    assert "passed" in data
    assert "summary" in data
    assert "findings" in data


async def test_audit_logs_endpoint(client: AsyncClient):
    resp = await client.get("/api/v1/security/audit/logs")
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "items" in data
    assert isinstance(data["items"], list)


async def test_audit_logs_with_filters(client: AsyncClient):
    resp = await client.get("/api/v1/security/audit/logs", params={
        "action": "auth.login",
        "severity": "info",
        "limit": 10,
        "offset": 0,
    })
    assert resp.status_code == 200


async def test_audit_logs_with_actor_id_filter(client: AsyncClient):
    """Exercise the actor_id filter branch (lines 78-79)."""
    resp = await client.get("/api/v1/security/audit/logs", params={
        "actor_id": "user-123",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert isinstance(data["items"], list)


async def test_compliance_report_endpoint(client: AsyncClient):
    resp = await client.get("/api/v1/security/compliance/report")
    assert resp.status_code == 200
    data = resp.json()
    assert "overall_status" in data
    assert "frameworks" in data
    assert "checks" in data


async def test_compliance_report_with_framework_filter(client: AsyncClient):
    resp = await client.get("/api/v1/security/compliance/report", params={"frameworks": "gdpr"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["frameworks"] == ["gdpr"]


async def test_compliance_report_invalid_framework(client: AsyncClient):
    resp = await client.get("/api/v1/security/compliance/report", params={"frameworks": "invalid"})
    assert resp.status_code == 400


async def test_create_api_key(client: AsyncClient):
    resp = await client.post("/api/v1/security/api-keys", json={
        "name": "Test Key",
        "role": "admin",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Test Key"
    assert data["role"] == "admin"
    assert data["key"].startswith("bsn-")
    assert "id" in data


async def test_create_api_key_with_expiry(client: AsyncClient):
    resp = await client.post("/api/v1/security/api-keys", json={
        "name": "Expiring Key",
        "role": "viewer",
        "expires_in_days": 30,
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["expires_at"] is not None


async def test_create_api_key_invalid_role(client: AsyncClient):
    resp = await client.post("/api/v1/security/api-keys", json={
        "name": "Bad Key",
        "role": "superadmin",
    })
    assert resp.status_code == 400


async def test_list_api_keys(client: AsyncClient):
    # Create an admin key first (bootstrap mode allows this)
    create_resp = await client.post("/api/v1/security/api-keys", json={"name": "Admin Key", "role": "admin"})
    admin_key = create_resp.json()["key"]

    # Now list keys using the admin key for auth
    resp = await client.get(
        "/api/v1/security/api-keys",
        headers={"Authorization": f"Bearer {admin_key}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    # Key should not expose full key
    for key in data:
        assert "key_prefix" in key


async def test_revoke_api_key(client: AsyncClient):
    # Create an admin key (bootstrap mode)
    admin_resp = await client.post("/api/v1/security/api-keys", json={"name": "Admin Key", "role": "admin"})
    admin_key = admin_resp.json()["key"]
    auth_headers = {"Authorization": f"Bearer {admin_key}"}

    # Create a viewer key to revoke
    create_resp = await client.post(
        "/api/v1/security/api-keys",
        json={"name": "Revoke Test", "role": "viewer"},
        headers=auth_headers,
    )
    key_id = create_resp.json()["id"]

    # Revoke it
    resp = await client.delete(f"/api/v1/security/api-keys/{key_id}", headers=auth_headers)
    assert resp.status_code == 204

    # Verify it shows as inactive
    list_resp = await client.get("/api/v1/security/api-keys", headers=auth_headers)
    keys = list_resp.json()
    revoked_key = next(k for k in keys if k["id"] == key_id)
    assert revoked_key["is_active"] is False


async def test_revoke_nonexistent_key(client: AsyncClient):
    import uuid

    resp = await client.delete(f"/api/v1/security/api-keys/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_encryption_test_endpoint(client: AsyncClient):
    resp = await client.post("/api/v1/security/encrypt-test")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["encrypted_length"] > 0


# ── Direct unit tests for uncovered lines ────────────────────────────


async def test_run_security_scan_direct(db_session: AsyncSession) -> None:
    """Directly call run_security_scan to cover lines 49-51."""
    result = await run_security_scan(_auth=None, db=db_session)
    assert result.passed is not None
    assert result.scan_timestamp is not None


async def test_list_audit_logs_with_data(db_session: AsyncSession) -> None:
    """Directly call list_audit_logs with existing log entries to cover lines 82-86."""
    audit_logger = AuditLogger(db_session)
    await audit_logger.log(AuditAction.security_audit_requested, details={"test": True})
    await db_session.commit()

    result = await list_audit_logs(
        action=None, severity=None, actor_id=None,
        limit=50, offset=0, _auth=None, db=db_session,
    )
    assert result.total >= 1
    assert len(result.items) >= 1


async def test_create_api_key_direct(db_session: AsyncSession) -> None:
    """Directly call create_api_key to cover lines 155-167."""
    request = APIKeyCreateRequest(name="Direct Test", role="viewer")
    result = await create_api_key(request=request, _auth=None, db=db_session)
    assert result.name == "Direct Test"
    assert result.role == "viewer"
    assert result.key.startswith("bsn-")


async def test_list_api_keys_direct(db_session: AsyncSession) -> None:
    """Directly call list_api_keys to cover line 184."""
    # Create a key first
    request = APIKeyCreateRequest(name="List Test", role="admin")
    await create_api_key(request=request, _auth=None, db=db_session)

    result = await list_api_keys(_auth=None, db=db_session)
    assert len(result) >= 1
    assert result[0].name == "List Test"


async def test_revoke_api_key_direct(db_session: AsyncSession) -> None:
    """Directly call revoke_api_key to cover lines 196-211."""
    # Create a key first
    request = APIKeyCreateRequest(name="Revoke Test", role="operator")
    created = await create_api_key(request=request, _auth=None, db=db_session)

    # Revoke it
    await revoke_api_key(key_id=created.id, _auth=None, db=db_session)

    # Verify inactive
    result = await list_api_keys(_auth=None, db=db_session)
    revoked = next(k for k in result if k.id == created.id)
    assert revoked.is_active is False
