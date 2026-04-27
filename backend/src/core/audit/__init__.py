"""Audit subpackage.

Two distinct concerns live side-by-side here:

* ``audit_sink`` — the surviving non-LLM BSupervisor audit channel
  (Phase 0 P0.7 retired the LLM path, BSGateway absorbs run.pre/post).
* ``emitter`` — the new ``bsvibe-audit`` outbox-pattern emitter wired
  into BSNexus surfaces (Phase Audit Batch 2).

The two do **not** share plumbing — they target different sinks
(BSupervisor LLM-shaped events vs. BSVibe-Auth's audit log) — but they
share the umbrella ``audit/`` namespace because both belong to "what
gets recorded about a domain action".
"""

from backend.src.core.audit.audit_sink import (
    AuditResult,
    AuditSink,
    BSupervisorAuditSink,
    NoopAuditSink,
    emit_post_async,
    resolve_audit_sink,
)
from backend.src.core.audit.emitter import (
    AuditActor,
    AuditResource,
    actor_from_user,
    actor_orchestrator,
    actor_system,
    build_relay,
    get_emitter,
    resource_decision,
    resource_deliverable,
    resource_project,
    resource_request,
    resource_run,
    safe_emit,
)

__all__ = [
    # bsvibe-audit emitter (Phase Audit Batch 2)
    "AuditActor",
    "AuditResource",
    "actor_from_user",
    "actor_orchestrator",
    "actor_system",
    "build_relay",
    "get_emitter",
    "resource_decision",
    "resource_deliverable",
    "resource_project",
    "resource_request",
    "resource_run",
    "safe_emit",
    # Existing audit_sink (BSupervisor non-LLM path, P0.7 surviving)
    "AuditResult",
    "AuditSink",
    "BSupervisorAuditSink",
    "NoopAuditSink",
    "emit_post_async",
    "resolve_audit_sink",
]
