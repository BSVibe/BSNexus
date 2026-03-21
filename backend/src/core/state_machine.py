from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.models import Phase, PhaseStatus, Task, TaskHistory, TaskStatus
from backend.src.queue.streams import RedisStreamManager
from backend.src.repositories.task_repository import TaskRepository

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from backend.src.core.prompt_security import PromptSigner


class TaskStateMachine:
    """State machine for managing task status transitions."""

    TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
        TaskStatus.waiting: {TaskStatus.ready},
        TaskStatus.ready: {TaskStatus.in_progress},
        TaskStatus.in_progress: {TaskStatus.review, TaskStatus.ready, TaskStatus.redesign},
        TaskStatus.review: {TaskStatus.done, TaskStatus.ready, TaskStatus.in_progress, TaskStatus.redesign},
        TaskStatus.done: set(),
        TaskStatus.redesign: {TaskStatus.waiting},
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

        # 2. Record history (requires db_session)
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

        # 3. Update task status + version (optimistic locking)
        task.status = new_status
        task.version += 1

        # 4. Execute side effects
        await self._execute_side_effects(
            task, old_status, new_status, db_session, stream_manager, reason=reason, **kwargs
        )

        # 5. Publish board event
        if stream_manager is not None:
            await stream_manager.publish_board_event(
                "task_transition",
                {
                    "task_id": str(task.id),
                    "project_id": str(task.project_id),
                    "from_status": old_status.value,
                    "to_status": new_status.value,
                    "actor": actor,
                },
            )

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
            TaskStatus.ready: self._on_ready,
            TaskStatus.in_progress: self._on_in_progress,
            TaskStatus.done: self._on_done,
            TaskStatus.redesign: self._on_redesign,
        }
        handler = side_effect_map.get(new_status)
        if handler is not None:
            await handler(task, old_status=old_status, db_session=db_session, stream_manager=stream_manager, **kwargs)

    async def _on_ready(
        self,
        task: Task,
        *,
        old_status: Optional[TaskStatus] = None,
        db_session: Optional[AsyncSession] = None,
        stream_manager: Optional[RedisStreamManager] = None,
        **kwargs: Any,
    ) -> None:
        """Reset execution fields when retrying.

        Note: qa_feedback_history is intentionally preserved — the orchestrator
        appends failure context before this transition so the next attempt can
        reference prior feedback.
        """
        if old_status in (TaskStatus.in_progress, TaskStatus.review):
            task.error_message = None
            task.qa_result = None
            task.started_at = None

    async def _on_in_progress(
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

    async def _on_redesign(
        self,
        task: Task,
        *,
        old_status: Optional[TaskStatus] = None,
        db_session: Optional[AsyncSession] = None,
        stream_manager: Optional[RedisStreamManager] = None,
        **kwargs: Any,
    ) -> None:
        """Handle escalation to Architect: publish escalation event."""
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
                candidate.status = TaskStatus.ready
                candidate.version += 1

                history = TaskHistory(
                    task_id=candidate.id,
                    from_status=old_status.value,
                    to_status=TaskStatus.ready.value,
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
