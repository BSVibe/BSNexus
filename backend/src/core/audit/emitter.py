"""``bsvibe-audit`` integration for BSNexus — emitter singleton + helpers.

This module sits next to ``audit_sink`` (which is a different concept
— the surviving non-LLM BSupervisor audit channel, P0.7) but talks to
the **bsvibe-audit** outbox-pattern audit logger. They do not share
plumbing; this one writes ``audit_outbox`` rows in the caller's session,
the other one POSTs to BSupervisor.

Key design decisions
~~~~~~~~~~~~~~~~~~~~

1. **Module-level singleton emitter.** ``AuditEmitter`` is stateless
   apart from the underlying ``OutboxStore``. Threading a Depends()
   through every router would touch dozens of call sites; a module
   import gives the same effect without churn.
2. **Failure-isolated emit.** ``safe_emit`` swallows + logs any exception
   from the audit path. Audit must never block a domain mutation —
   BSVibe_Audit_Design.md §3.1 explicitly says the outbox protects
   against audit_store downtime, but a serialization bug in our payload
   (or a SQLAlchemy session in a weird state) must not cascade into a
   500 to the user. Tests assert this.
3. **Helpers for the standard cases**: ``actor_from_user`` /
   ``actor_system`` / ``actor_orchestrator`` / ``resource_*``. These are
   thin so call sites stay readable.

The emitter writes into the caller's ``AsyncSession``; the caller's
own ``commit()`` is what makes the outbox row durable. This makes
the audit row atomic with the domain mutation, exactly as the design
doc requires.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from bsvibe_audit import (
    AuditActor,
    AuditEmitter,
    AuditEventBase,
    AuditResource,
    AuditSettings,
    OutboxRelay,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = structlog.get_logger(__name__)


# ── Module-level singletons ─────────────────────────────────────────
# A single ``AuditEmitter`` per process is enough; the emitter is
# stateless apart from the underlying store. ``OutboxRelay`` *is*
# stateful (background asyncio task) — it is constructed in lifespan
# and stashed on ``app.state`` so tests can override it.
_emitter = AuditEmitter()


def get_emitter() -> AuditEmitter:
    """Return the shared :class:`AuditEmitter` for direct emit calls."""
    return _emitter


def build_relay(
    *,
    settings: AuditSettings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None,
) -> OutboxRelay:
    """Build a :class:`OutboxRelay` configured from env.

    Tests inject ``settings`` to flip ``relay_enabled`` without touching
    process-wide env. When ``BSVIBE_AUTH_AUDIT_URL`` is empty the relay
    returns a disabled no-op singleton (its ``start`` / ``stop`` are
    safe to call regardless).
    """
    cfg = settings if settings is not None else AuditSettings()
    return OutboxRelay.from_settings(cfg, session_factory=session_factory)


# ── Emit wrapper ────────────────────────────────────────────────────
async def safe_emit(
    event: AuditEventBase,
    *,
    session: AsyncSession,
) -> None:
    """Emit ``event`` and log+swallow any exception.

    The outbox INSERT participates in the caller's transaction. If the
    caller later rolls back, the outbox row rolls back with it — that
    is the design contract (BSVibe_Audit_Design.md §3.1).

    ``safe_emit`` shields the producer from emit-time bugs (bad payload,
    closed session, etc.). The audit channel must degrade silently
    rather than fail the user-facing operation.
    """
    try:
        await _emitter.emit(event, session=session)
    except Exception:  # noqa: BLE001 — audit must never break the domain path
        logger.warning(
            "audit_emit_failed",
            event_type=event.event_type,
            tenant_id=event.tenant_id,
            exc_info=True,
        )


# ── Actor helpers ───────────────────────────────────────────────────
def actor_from_user(user: Any) -> AuditActor:
    """Build an :class:`AuditActor` from a ``BSVibeUser``-shaped object.

    ``user`` mirrors the auth dependency's return shape: ``id`` (uuid or
    str), optional ``email``. Missing email is fine — auth.user.created
    is the only event that requires it and that one is BSVibe-Auth's
    responsibility, not BSNexus'.
    """
    user_id = getattr(user, "id", None)
    if user_id is None:
        return actor_system()
    return AuditActor(
        type="user",
        id=str(user_id),
        email=getattr(user, "email", None),
    )


def actor_system() -> AuditActor:
    """Background dispatcher / replanner / state-machine self-transitions."""
    return AuditActor(type="system", id="bsnexus")


def actor_orchestrator() -> AuditActor:
    """Run state transitions driven by ``RunOrchestrator`` itself."""
    return AuditActor(type="service", id="bsnexus.orchestrator")


# ── Resource helpers ────────────────────────────────────────────────
def resource_project(project_id: uuid.UUID | str) -> AuditResource:
    return AuditResource(type="project", id=str(project_id))


def resource_request(request_id: uuid.UUID | str) -> AuditResource:
    return AuditResource(type="request", id=str(request_id))


def resource_run(run_id: uuid.UUID | str) -> AuditResource:
    return AuditResource(type="execution_run", id=str(run_id))


def resource_deliverable(deliverable_id: uuid.UUID | str) -> AuditResource:
    return AuditResource(type="deliverable", id=str(deliverable_id))


def resource_decision(decision_id: uuid.UUID | str) -> AuditResource:
    return AuditResource(type="decision", id=str(decision_id))


__all__ = [
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
]
