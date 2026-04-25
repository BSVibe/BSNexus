"""Audit sink — optional BSupervisor integration with local fallback."""

from backend.src.core.audit.audit_sink import (
    AuditResult,
    AuditSink,
    BSupervisorAuditSink,
    NoopAuditSink,
    emit_post_async,
    resolve_audit_sink,
)

__all__ = [
    "AuditResult",
    "AuditSink",
    "BSupervisorAuditSink",
    "NoopAuditSink",
    "emit_post_async",
    "resolve_audit_sink",
]
