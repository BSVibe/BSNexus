from __future__ import annotations

import json
import structlog
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.models import (
    ActivityLevel,
    Phase,
    PhaseStatus,
    Task,
    TaskActivity,
    TaskHistory,
    TaskStatus,
)
from backend.src.queue.streams import RedisStreamManager
from backend.src.repositories.task_repository import TaskRepository

logger = structlog.get_logger(__name__)

if TYPE_CHECKING:
    from backend.src.core.prompt_security import PromptSigner


class TaskStateMachine:
    """State machine for managing task status transitions."""

    TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
        TaskStatus.pending: {TaskStatus.running, TaskStatus.blocked},
        TaskStatus.running: {TaskStatus.done, TaskStatus.pending, TaskStatus.blocked},
        TaskStatus.blocked: {TaskStatus.pending},
        TaskStatus.done: set(),
    }

    def __init__(
        self,
        prompt_signer: PromptSigner | None = None,
    ) -> None:
        self.prompt_signer = prompt_signer

    def can_transition(self, from_status: TaskStatus, to_status: TaskStatus) -> bool:
        """Check if a transition is allowed."""
        allowed = self.TRANSITIONS.get(from_status, set())
        return to_status in allowed

    async def transition(
        self,
        task: Task,
        new_status: TaskStatus,
        reason: Optional[str] = None,
        actor: str = "system",
        db_session: Optional[AsyncSession] = None,
        stream_manager: Optional[RedisStreamManager] = None,
        **kwargs: Any,
    ) -> Task:
        """Execute a state transition with side effects."""
        old_status = task.status

        # 1. Validate transition
        if not self.can_transition(old_status, new_status):
            logger.error("Invalid transition: %s -> %s for task %s", old_status.value, new_status.value, task.id)
            raise ValueError(f"Invalid transition: {old_status.value} -> {new_status.value}")

        logger.info(
            "Task %s: %s -> %s (actor=%s, reason=%s)", task.id, old_status.value, new_status.value, actor, reason
        )

        # 2. Record history + milestone activity (requires db_session)
        if db_session is not None:
            history = TaskHistory(
                task_id=task.id,
                from_status=old_status.value,
                to_status=new_status.value,
                actor=actor,
                reason=reason,
                extra_metadata=kwargs if kwargs else None,
            )
            db_session.add(history)

            db_session.add(
                TaskActivity(
                    task_id=task.id,
                    project_id=task.project_id,
                    agent_id=task.agent_id,
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

        # 3. Update task status + version (optimistic locking)
        task.status = new_status
        task.version += 1

        # 4. Execute side effects
        await self._execute_side_effects(
            task, old_status, new_status, db_session, stream_manager, reason=reason, **kwargs
        )

        # 5. Publish to BOTH plan and chat SSE streams so all frontends react.
        if stream_manager is not None:
            event_data = {
                "task_id": str(task.id),
                "from_status": old_status.value,
                "to_status": new_status.value,
                "actor": actor,
                "agent_id": str(task.agent_id) if task.agent_id else None,
            }
            # Plan view stream (task_transition, phase_advanced)
            await stream_manager.publish_project_event(
                str(task.project_id), "task_transition", event_data,
            )
            # Chat sidebar stream (so agent status dots update)
            chat_stream = RedisStreamManager.chat_events_stream(str(task.project_id))
            try:
                await stream_manager.publish(chat_stream, {"event": "task_transition", "data": event_data})
            except Exception:  # noqa: BLE001
                pass  # best-effort — don't break state machine

        return task

    async def _execute_side_effects(
        self,
        task: Task,
        old_status: TaskStatus,
        new_status: TaskStatus,
        db_session: Optional[AsyncSession],
        stream_manager: Optional[RedisStreamManager],
        **kwargs: Any,
    ) -> None:
        """Dispatch side effects based on the new status."""
        side_effect_map = {
            TaskStatus.pending: self._on_pending,
            TaskStatus.running: self._on_running,
            TaskStatus.done: self._on_done,
            TaskStatus.blocked: self._on_blocked,
        }
        handler = side_effect_map.get(new_status)
        if handler is not None:
            await handler(task, old_status=old_status, db_session=db_session, stream_manager=stream_manager, **kwargs)

    async def _on_pending(
        self,
        task: Task,
        *,
        old_status: Optional[TaskStatus] = None,
        db_session: Optional[AsyncSession] = None,
        stream_manager: Optional[RedisStreamManager] = None,
        **kwargs: Any,
    ) -> None:
        """Reset execution fields when retrying.

        Note: qa_feedback_history is intentionally preserved — callers
        append failure context before this transition so the next attempt
        can reference prior feedback.
        """
        if old_status == TaskStatus.running:
            task.error_message = None
            task.qa_result = None
            task.started_at = None

    async def _on_running(
        self,
        task: Task,
        *,
        old_status: Optional[TaskStatus] = None,
        db_session: Optional[AsyncSession] = None,
        stream_manager: Optional[RedisStreamManager] = None,
        **kwargs: Any,
    ) -> None:
        """Set started_at timestamp."""
        task.started_at = datetime.now(timezone.utc)

    async def _on_done(
        self,
        task: Task,
        *,
        old_status: Optional[TaskStatus] = None,
        db_session: Optional[AsyncSession] = None,
        stream_manager: Optional[RedisStreamManager] = None,
        **kwargs: Any,
    ) -> None:
        """Set completed_at and promote dependent tasks."""
        task.completed_at = datetime.now(timezone.utc)
        if db_session is not None:
            repo = TaskRepository(db_session)
            await self._promote_dependents(task, repo, db_session)

    async def _on_blocked(
        self,
        task: Task,
        *,
        old_status: Optional[TaskStatus] = None,
        db_session: Optional[AsyncSession] = None,
        stream_manager: Optional[RedisStreamManager] = None,
        **kwargs: Any,
    ) -> None:
        """Handle escalation: publish event so a planning agent can react."""
        reason = kwargs.get("reason")
        if reason is not None:
            task.error_message = reason

        if stream_manager is not None:
            await stream_manager.publish(
                RedisStreamManager.TASKS_ESCALATION,
                {
                    "task_id": str(task.id),
                    "project_id": str(task.project_id),
                    "title": task.title,
                    "retry_count": str(task.retry_count),
                    "qa_feedback_history": json.dumps(task.qa_feedback_history or []),
                    "error_message": task.error_message or "",
                },
            )

    # -- Dependency Methods ----------------------------------------------------

    async def check_dependencies_met(self, task: Task, db_session: AsyncSession) -> bool:
        """Check if all dependency tasks are in DONE status."""
        repo = TaskRepository(db_session)
        return await repo.check_dependencies_met(task.id)

    async def _promote_dependents(self, task: Task, repo: TaskRepository, db_session: AsyncSession) -> list[Task]:
        """Promote WAITING tasks that depend on the completed task to READY."""
        waiting_tasks = await repo.find_waiting_dependents(task.id)

        promoted: list[Task] = []
        for candidate in waiting_tasks:
            if not await self._is_phase_active(candidate.phase_id, db_session):
                continue
            if await repo.check_dependencies_met(candidate.id):
                old_status = candidate.status
                candidate.status = TaskStatus.pending
                candidate.version += 1

                history = TaskHistory(
                    task_id=candidate.id,
                    from_status=old_status.value,
                    to_status=TaskStatus.pending.value,
                    actor="system",
                    reason=f"All dependencies met (triggered by task {task.id})",
                )
                db_session.add(history)
                promoted.append(candidate)

        return promoted

    async def _is_phase_active(self, phase_id: uuid.UUID, db_session: AsyncSession) -> bool:
        """Check if the given phase is in active status."""
        result = await db_session.execute(select(Phase.status).where(Phase.id == phase_id))
        status = result.scalar_one_or_none()
        return status == PhaseStatus.active

    async def promote_dependents(self, task: Task, db_session: AsyncSession) -> list[Task]:
        """Promote WAITING tasks that depend on the completed task to READY (public API)."""
        repo = TaskRepository(db_session)
        return await self._promote_dependents(task, repo, db_session)


# -- Milestone helpers --------------------------------------------------------


_MILESTONE_EVENT_TYPES: dict[TaskStatus, str] = {
    TaskStatus.pending: "task_reset",
    TaskStatus.running: "task_started",
    TaskStatus.done: "task_completed",
    TaskStatus.blocked: "task_blocked",
}


def _milestone_event_type(new_status: TaskStatus) -> str:
    return _MILESTONE_EVENT_TYPES.get(new_status, "task_transition")


def _milestone_summary(new_status: TaskStatus, actor: str, reason: Optional[str]) -> str:
    label = {
        TaskStatus.pending: "Reset to pending",
        TaskStatus.running: "Started",
        TaskStatus.done: "Completed",
        TaskStatus.blocked: "Blocked",
    }.get(new_status, f"Transitioned to {new_status.value}")
    if reason:
        return f"{label} by {actor}: {reason}"
    return f"{label} by {actor}"
