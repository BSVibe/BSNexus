"""Security API endpoints for audit logs, security scans, and compliance."""

from typing import Optional

from bsvibe_auth import BSVibeUser
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.config import settings
from backend.src.core.auth import Permission, require_permission
from backend.src.core.audit_logger import AuditAction, AuditLog, AuditLogger
from backend.src.core.compliance import ComplianceFramework, ComplianceManager
from backend.src.core.encryption import EncryptionManager
from backend.src.core.security_auditor import SecurityAuditor
from backend.src.schemas import (
    AuditLogListResponse,
    AuditLogResponse,
    ComplianceReportResponse,
    SecurityReportResponse,
)
from backend.src.storage.database import get_db

router = APIRouter(prefix="/api/v1/security", tags=["security"])


# ── Security Audit ────────────────────────────────────────────────────


@router.get("/audit/scan", response_model=SecurityReportResponse)
async def run_security_scan(
    _auth: BSVibeUser = Depends(require_permission(Permission.admin_security)),
    db: AsyncSession = Depends(get_db),
) -> SecurityReportResponse:
    """Run a security vulnerability scan against the current configuration."""
    auditor = SecurityAuditor(settings)
    report = auditor.run_full_scan()

    # Log the audit event
    audit_logger = AuditLogger(db)
    await audit_logger.log(
        AuditAction.security_audit_requested,
        details={"summary": report.summary, "passed": report.passed},
    )
    await db.commit()

    return SecurityReportResponse(**report.to_dict())


# ── Audit Logs ────────────────────────────────────────────────────────


@router.get("/audit/logs", response_model=AuditLogListResponse)
async def list_audit_logs(
    action: Optional[str] = Query(None, description="Filter by action type"),
    severity: Optional[str] = Query(None, description="Filter by severity"),
    actor_id: Optional[str] = Query(None, description="Filter by actor ID"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _auth: BSVibeUser = Depends(require_permission(Permission.admin_audit)),
    db: AsyncSession = Depends(get_db),
) -> AuditLogListResponse:
    """List audit log entries with optional filtering."""
    query = select(AuditLog)
    count_query = select(func.count()).select_from(AuditLog)

    if action:
        query = query.where(AuditLog.action == action)
        count_query = count_query.where(AuditLog.action == action)
    if severity:
        query = query.where(AuditLog.severity == severity)
        count_query = count_query.where(AuditLog.severity == severity)
    if actor_id:
        query = query.where(AuditLog.actor_id == actor_id)
        count_query = count_query.where(AuditLog.actor_id == actor_id)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(AuditLog.timestamp.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    logs = result.scalars().all()

    return AuditLogListResponse(
        total=total,
        items=[AuditLogResponse.model_validate(log) for log in logs],
    )


# ── Compliance ────────────────────────────────────────────────────────


@router.get("/compliance/report", response_model=ComplianceReportResponse)
async def get_compliance_report(
    frameworks: Optional[str] = Query(None, description="Comma-separated frameworks: gdpr,soc2"),
    _auth: BSVibeUser = Depends(require_permission(Permission.admin_security)),
    db: AsyncSession = Depends(get_db),
) -> ComplianceReportResponse:
    """Generate a compliance assessment report."""
    framework_list = None
    if frameworks:
        framework_list = []
        for f in frameworks.split(","):
            f = f.strip().lower()
            try:
                framework_list.append(ComplianceFramework(f))
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Unknown framework: {f}. Valid: gdpr, soc2, hipaa, iso27001",
                )

    manager = ComplianceManager(db)
    report = await manager.generate_compliance_report(framework_list)
    return ComplianceReportResponse(**report)


# ── Encryption Utilities ──────────────────────────────────────────────


@router.post("/encrypt-test")
async def test_encryption(
    _auth: BSVibeUser = Depends(require_permission(Permission.admin_security)),
) -> dict:
    """Test that encryption/decryption round-trips correctly (for ops verification)."""
    enc = EncryptionManager(settings.encryption_key)
    test_value = "encryption-test-value"
    encrypted = enc.encrypt_value(test_value)
    decrypted = enc.decrypt_value(encrypted)
    return {
        "status": "ok" if decrypted == test_value else "error",
        "encrypted_length": len(encrypted),
    }
