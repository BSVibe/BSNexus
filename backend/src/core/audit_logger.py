"""Audit logging for compliance tracking and security monitoring."""

import enum
import json
import structlog
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, Index, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from backend.src.storage.database import Base


logger = structlog.get_logger("bsnexus.audit")


class AuditAction(str, enum.Enum):
    # Authentication events
    auth_login = "auth.login"
    auth_logout = "auth.logout"
    auth_failed = "auth.failed"
    auth_token_created = "auth.token_created"
    auth_token_revoked = "auth.token_revoked"

    # Data access events
    data_read = "data.read"
    data_created = "data.created"
    data_updated = "data.updated"
    data_deleted = "data.deleted"
    data_exported = "data.exported"

    # Admin events
    admin_settings_changed = "admin.settings_changed"
    admin_config_changed = "admin.config_changed"

    # Security events
    security_rate_limited = "security.rate_limited"
    security_invalid_input = "security.invalid_input"
    security_unauthorized = "security.unauthorized"
    security_prompt_tampered = "security.prompt_tampered"
    security_audit_requested = "security.audit_requested"

    # Task events
    task_state_changed = "task.state_changed"
    task_assigned = "task.assigned"


class AuditSeverity(str, enum.Enum):
    info = "info"
    warning = "warning"
    error = "error"
    critical = "critical"


class AuditLog(Base):
    """Persistent audit log entries for compliance and security monitoring."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_action_timestamp", "action", "timestamp"),
        Index("ix_audit_logs_actor_timestamp", "actor_id", "timestamp"),
        Index("ix_audit_logs_severity", "severity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    action: Mapped[AuditAction] = mapped_column(Enum(AuditAction), nullable=False)
    severity: Mapped[AuditSeverity] = mapped_column(Enum(AuditSeverity), nullable=False, default=AuditSeverity.info)
    actor_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actor_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    resource_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    request_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    request_method: Mapped[str | None] = mapped_column(String(10), nullable=True)


class AuditLogger:
    """Service for recording audit events to the database and log stream."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def log(
        self,
        action: AuditAction,
        *,
        severity: AuditSeverity = AuditSeverity.info,
        actor_id: str | None = None,
        actor_type: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        details: dict[str, Any] | None = None,
        request_path: str | None = None,
        request_method: str | None = None,
    ) -> AuditLog:
        """Record an audit event."""
        entry = AuditLog(
            action=action,
            severity=severity,
            actor_id=actor_id,
            actor_type=actor_type,
            resource_type=resource_type,
            resource_id=resource_id,
            ip_address=ip_address,
            user_agent=user_agent,
            details=details,
            request_path=request_path,
            request_method=request_method,
        )
        self._db.add(entry)
        await self._db.flush()

        # Also emit to structured logging for external log aggregation
        log_data = {
            "audit_id": str(entry.id),
            "action": action.value,
            "severity": severity.value,
            "actor_id": actor_id,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "ip_address": ip_address,
            "request_path": request_path,
        }
        if details:
            log_data["details"] = details

        log_method = getattr(logger, severity.value, logger.info)
        log_method("audit_event: %s", json.dumps(log_data, default=str))

        return entry

    async def log_security_event(
        self,
        action: AuditAction,
        *,
        ip_address: str | None = None,
        details: dict[str, Any] | None = None,
        request_path: str | None = None,
    ) -> AuditLog:
        """Convenience method for security events (always severity=warning+)."""
        severity = AuditSeverity.warning
        if action in (AuditAction.security_prompt_tampered, AuditAction.security_unauthorized):
            severity = AuditSeverity.critical

        return await self.log(
            action,
            severity=severity,
            ip_address=ip_address,
            details=details,
            request_path=request_path,
        )

    async def log_data_access(
        self,
        action: AuditAction,
        *,
        actor_id: str | None = None,
        resource_type: str,
        resource_id: str,
        details: dict[str, Any] | None = None,
    ) -> AuditLog:
        """Convenience method for data access events (GDPR compliance)."""
        return await self.log(
            action,
            actor_id=actor_id,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
        )
