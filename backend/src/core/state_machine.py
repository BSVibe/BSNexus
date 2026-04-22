"""State machine for ExecutionRun lifecycle.

Transitions:

    pending → running → done
       ↑         ↓
       └────blocked

Invariants:
- ``done`` is terminal.
- ``blocked`` can only go back through ``pending``.
- Every transition writes an ExecutionRunHistory row, a milestone
  ExecutionRunActivity row, and publishes to Redis Streams so SSE
  subscribers get a live update.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.models import (
    ActivityLevel,
    ExecutionRun,
    ExecutionRunActivity,
    ExecutionRunHistory,
    RunStatus,
)
from backend.src.queue.streams import RedisStreamManager

logger = structlog.get_logger(__name__)


class RunStateMachine:
    """State machine for ExecutionRun transitions."""

    TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
        RunStatus.pending: {RunStatus.running, RunStatus.blocked},
        RunStatus.running: {RunStatus.done, RunStatus.pending, RunStatus.blocked},
        RunStatus.blocked: {RunStatus.pending},
        RunStatus.done: set(),
    }

    def can_transition(self, from_status: RunStatus, to_status: RunStatus) -> bool:
        return to_status in self.TRANSITIONS.get(from_status, set())

    async def transition(
        self,
        run: ExecutionRun,
        new_status: RunStatus,
        *,
        reason: str | None = None,
        actor: str = "system",
        db_session: AsyncSession | None = None,
        stream_manager: RedisStreamManager | None = None,
        **kwargs: Any,
    ) -> ExecutionRun:
        old_status = run.status

        if not self.can_transition(old_status, new_status):
            logger.error(
                "invalid_run_transition",
                run_id=str(run.id),
                from_status=old_status.value,
                to_status=new_status.value,
            )
            raise ValueError(
                f"Invalid transition: {old_status.value} → {new_status.value}"
            )

        logger.info(
            "run_transition",
            run_id=str(run.id),
            from_status=old_status.value,
            to_status=new_status.value,
            actor=actor,
            reason=reason,
        )

        if db_session is not None:
            db_session.add(
                ExecutionRunHistory(
                    run_id=run.id,
                    from_status=old_status,
                    to_status=new_status,
                    actor=actor,
                    reason=reason,
                    extra_metadata=kwargs or None,
                )
            )
            db_session.add(
                ExecutionRunActivity(
                    run_id=run.id,
                    project_id=run.project_id,
                    level=ActivityLevel.milestone,
                    event_type=_milestone_event_type(new_status),
                    summary=_milestone_summary(new_status, actor, reason),
                    detail={
                        "from_status": old_status.value,
                        "to_status": new_status.value,
                        "actor": actor,
                        **({"reason": reason} if reason else {}),
                    },
                )
            )

        run.status = new_status
        await self._side_effects(run, old_status, new_status, reason=reason, **kwargs)

        if stream_manager is not None:
            event = {
                "run_id": str(run.id),
                "request_id": str(run.request_id),
                "from_status": old_status.value,
                "to_status": new_status.value,
                "actor": actor,
            }
            await stream_manager.publish_project_event(
                str(run.project_id), "run_transition", event
            )

        return run

    async def _side_effects(
        self,
        run: ExecutionRun,
        old_status: RunStatus,
        new_status: RunStatus,
        *,
        reason: str | None = None,
        **_: Any,
    ) -> None:
        if new_status == RunStatus.running:
            run.started_at = datetime.now(timezone.utc)
        elif new_status == RunStatus.done:
            run.completed_at = datetime.now(timezone.utc)
        elif new_status == RunStatus.blocked:
            if reason:
                run.error_message = reason
        elif new_status == RunStatus.pending and old_status == RunStatus.running:
            run.error_message = None
            run.started_at = None


_MILESTONE_EVENT_TYPES: dict[RunStatus, str] = {
    RunStatus.pending: "run_reset",
    RunStatus.running: "run_started",
    RunStatus.done: "run_completed",
    RunStatus.blocked: "run_blocked",
}


def _milestone_event_type(new_status: RunStatus) -> str:
    return _MILESTONE_EVENT_TYPES.get(new_status, "run_transition")


def _milestone_summary(
    new_status: RunStatus, actor: str, reason: str | None
) -> str:
    label = {
        RunStatus.pending: "Reset to pending",
        RunStatus.running: "Started",
        RunStatus.done: "Completed",
        RunStatus.blocked: "Blocked",
    }.get(new_status, f"Transitioned to {new_status.value}")
    if reason:
        return f"{label} by {actor}: {reason}"
    return f"{label} by {actor}"


# Convenience: check if a set of run IDs have all finished.
async def all_runs_done(
    run_ids: list[uuid.UUID], db_session: AsyncSession
) -> bool:
    from sqlalchemy import func, select

    if not run_ids:
        return True
    stmt = select(func.count()).select_from(ExecutionRun).where(
        ExecutionRun.id.in_(run_ids),
        ExecutionRun.status != RunStatus.done,
    )
    result = await db_session.execute(stmt)
    return (result.scalar() or 0) == 0
