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
    from backend.src.utils.worker_registry import WorkerRegistry


class TaskStateMachine:
    """State machine for managing task status transitions."""

    TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
        TaskStatus.waiting: {TaskStatus.ready},
        TaskStatus.ready: {TaskStatus.queued},
        TaskStatus.queued: {TaskStatus.in_progress},
        TaskStatus.in_progress: {TaskStatus.review, TaskStatus.ready, TaskStatus.redesign},
        TaskStatus.review: {TaskStatus.done, TaskStatus.in_progress, TaskStatus.redesign},
        TaskStatus.done: set(),
        TaskStatus.redesign: {TaskStatus.waiting},
    }

    def __init__(
        self,
        prompt_signer: PromptSigner | None = None,
        worker_registry: WorkerRegistry | None = None,
    ) -> None:
        self.prompt_signer = prompt_signer
        self.worker_registry = worker_registry

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

        logger.info("Task %s: %s -> %s (actor=%s, reason=%s)", task.id, old_status.value, new_status.value, actor, reason)

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
            TaskStatus.queued: self._on_queued,
            TaskStatus.in_progress: self._on_in_progress,
            TaskStatus.review: self._on_review,
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
        """Reset execution fields when retrying from execution failure."""
        if old_status == TaskStatus.in_progress:
            task.worker_id = None
            task.reviewer_id = None
            task.error_message = None
            task.qa_result = None
            task.started_at = None

    async def _on_queued(
        self,
        task: Task,
        *,
        old_status: Optional[TaskStatus] = None,
        db_session: Optional[AsyncSession] = None,
        stream_manager: Optional[RedisStreamManager] = None,
        **kwargs: Any,
    ) -> None:
        """Publish task to the work queue via Redis Streams."""
        if stream_manager is not None:
            message: dict[str, Any] = {
                "task_id": str(task.id),
                "project_id": str(task.project_id),
                "priority": task.priority.value,
                "title": task.title,
            }
            # Include git metadata for workers
            if task.branch_name:
                message["branch_name"] = task.branch_name
            if db_session is not None:
                repo_path = await self._get_repo_path(task, db_session)
                if repo_path:
                    message["repo_path"] = repo_path
            # Include worker prompt
            if task.worker_prompt:
                prompt_text = (
                    task.worker_prompt if isinstance(task.worker_prompt, str) else json.dumps(task.worker_prompt)
                )
                if self.prompt_signer:
                    message["signed_worker_prompt"] = self.prompt_signer.sign(prompt_text)
                else:
                    message["worker_prompt"] = prompt_text
            await stream_manager.publish("tasks:queue", message)

    async def _on_in_progress(
        self,
        task: Task,
        *,
        old_status: Optional[TaskStatus] = None,
        db_session: Optional[AsyncSession] = None,
        stream_manager: Optional[RedisStreamManager] = None,
        **kwargs: Any,
    ) -> None:
        """Set worker_id and started_at timestamp."""
        if old_status == TaskStatus.review:
            # QA failure auto-retry: keep worker_id, refresh started_at, clear reviewer_id
            task.started_at = datetime.now(timezone.utc)
            task.reviewer_id = None
        else:
            # Normal first execution from queued
            worker_id = kwargs.get("worker_id")
            if worker_id is not None:
                task.worker_id = worker_id if isinstance(worker_id, uuid.UUID) else uuid.UUID(worker_id)
            task.started_at = datetime.now(timezone.utc)

            if self.worker_registry and worker_id is not None:
                try:
                    await self.worker_registry.set_busy(str(worker_id), str(task.id))
                except Exception:
                    pass  # Redis failure should not block state transition

    async def _on_review(
        self,
        task: Task,
        *,
        old_status: Optional[TaskStatus] = None,
        db_session: Optional[AsyncSession] = None,
        stream_manager: Optional[RedisStreamManager] = None,
        **kwargs: Any,
    ) -> None:
        """Set reviewer_id and publish to QA queue."""
        reviewer_id = kwargs.get("reviewer_id")
        if reviewer_id is not None:
            task.reviewer_id = reviewer_id if isinstance(reviewer_id, uuid.UUID) else uuid.UUID(reviewer_id)
        if stream_manager is not None:
            message: dict[str, Any] = {
                "task_id": str(task.id),
                "project_id": str(task.project_id),
                "title": task.title,
            }
            if task.reviewer_id:
                message["reviewer_id"] = str(task.reviewer_id)
            # Include git metadata for QA reviewers
            if task.branch_name:
                message["branch_name"] = task.branch_name
            if db_session is not None:
                repo_path = await self._get_repo_path(task, db_session)
                if repo_path:
                    message["repo_path"] = repo_path
            if self.prompt_signer and task.qa_prompt:
                prompt_text = task.qa_prompt if isinstance(task.qa_prompt, str) else json.dumps(task.qa_prompt)
                message["signed_qa_prompt"] = self.prompt_signer.sign(prompt_text)
            elif task.qa_prompt:
                prompt_text = task.qa_prompt if isinstance(task.qa_prompt, str) else json.dumps(task.qa_prompt)
                message["qa_prompt"] = prompt_text
            await stream_manager.publish("tasks:qa", message)

        if self.worker_registry and task.worker_id:
            try:
                await self.worker_registry.set_idle(str(task.worker_id))
            except Exception:
                pass  # Redis failure should not block state transition

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

        if self.worker_registry and task.reviewer_id:
            try:
                await self.worker_registry.set_idle(str(task.reviewer_id))
            except Exception:
                pass  # Redis failure should not block state transition

    async def _on_redesign(
        self,
        task: Task,
        *,
        old_status: Optional[TaskStatus] = None,
        db_session: Optional[AsyncSession] = None,
        stream_manager: Optional[RedisStreamManager] = None,
        **kwargs: Any,
    ) -> None:
        """Handle escalation to Architect: publish escalation event and release workers."""
        reason = kwargs.get("reason")
        if reason is not None:
            task.error_message = reason

        if stream_manager is not None:
            await stream_manager.publish(RedisStreamManager.TASKS_ESCALATION, {
                "task_id": str(task.id),
                "project_id": str(task.project_id),
                "title": task.title,
                "retry_count": str(task.retry_count),
                "qa_feedback_history": json.dumps(task.qa_feedback_history or []),
                "error_message": task.error_message or "",
            })

        # Release worker and reviewer
        if self.worker_registry:
            if task.worker_id:
                try:
                    await self.worker_registry.set_idle(str(task.worker_id))
                except Exception:
                    pass
            if task.reviewer_id:
                try:
                    await self.worker_registry.set_idle(str(task.reviewer_id))
                except Exception:
                    pass

    async def _get_repo_path(self, task: Task, db_session: AsyncSession) -> str:
        """Look up repo_path from the task's project."""
        from backend.src.repositories.project_repository import ProjectRepository

        project_repo = ProjectRepository(db_session)
        project = await project_repo.get_by_id(task.project_id, load_phases=False)
        return project.repo_path if project else ""

    # -- Dependency Methods ----------------------------------------------------

    async def check_dependencies_met(self, task: Task, db_session: AsyncSession) -> bool:
        """Check if all dependency tasks are in DONE status."""
        repo = TaskRepository(db_session)
        return await repo.check_dependencies_met(task.id)

    async def _promote_dependents(self, task: Task, repo: TaskRepository, db_session: AsyncSession) -> list[Task]:
        """Promote WAITING tasks that depend on the completed task to READY.

        Only promotes tasks whose phase is currently active.
        """
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
        result = await db_session.execute(
            select(Phase.status).where(Phase.id == phase_id)
        )
        status = result.scalar_one_or_none()
        return status == PhaseStatus.active

    async def promote_dependents(self, task: Task, db_session: AsyncSession) -> list[Task]:
        """Promote WAITING tasks that depend on the completed task to READY (public API)."""
        repo = TaskRepository(db_session)
        return await self._promote_dependents(task, repo, db_session)
