"""Tests for security API endpoints."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.api.security import list_audit_logs, run_security_scan
from backend.src.core.audit_logger import AuditAction, AuditLog, AuditLogger
from unittest.mock import MagicMock

pytestmark = pytest.mark.asyncio


async def test_security_scan_endpoint(client: AsyncClient):
    """Authenticated: admin user should access security scan."""
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
    """Exercise the actor_id filter branch."""
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


async def test_encryption_test_endpoint(client: AsyncClient):
    resp = await client.post("/api/v1/security/encrypt-test")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["encrypted_length"] > 0


# ── Direct unit tests for uncovered lines ────────────────────────────


def _make_mock_user(role: str = "admin") -> MagicMock:
    user = MagicMock()
    user.id = "test-user-id"
    user.app_metadata = {"role": role}
    return user


async def test_run_security_scan_direct(db_session: AsyncSession) -> None:
    """Directly call run_security_scan to cover audit log lines."""
    result = await run_security_scan(_auth=_make_mock_user(), db=db_session)
    assert result.passed is not None
    assert result.scan_timestamp is not None


async def test_list_audit_logs_with_data(db_session: AsyncSession) -> None:
    """Directly call list_audit_logs with existing log entries."""
    audit_logger = AuditLogger(db_session)
    await audit_logger.log(AuditAction.security_audit_requested, details={"test": True})
    await db_session.commit()

    result = await list_audit_logs(
        action=None, severity=None, actor_id=None,
        limit=50, offset=0, _auth=_make_mock_user(), db=db_session,
    )
    assert result.total >= 1
    assert len(result.items) >= 1
