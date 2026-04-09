from __future__ import annotations

import asyncio
import copy
import json
import structlog
import uuid
from collections.abc import AsyncGenerator, Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.config import settings
from backend.src.core.llm_client import LLMError, create_llm_client_from_project
from backend.src.core.state_machine import TaskStateMachine
from backend.src.core.task_runner import LocalTaskRunner
from backend.src.models import Phase, PhaseStatus, Task, TaskPriority, TaskSource, TaskStatus, TaskType
from backend.src.prompts.loader import get_prompt
from backend.src.queue.streams import RedisStreamManager
from backend.src.repositories.phase_repository import PhaseRepository
from backend.src.repositories.project_repository import ProjectRepository
from backend.src.repositories.task_repository import TaskRepository

logger = structlog.get_logger(__name__)

# Type alias: a callable that returns an async context manager yielding AsyncSession
SessionFactory = Callable[[], AsyncGenerator[AsyncSession, None]]

# Redis key TTLs
_INTERVENTION_KEY_TTL = 86400  # 24 hours
_RECOVERY_KEY_TTL = 86400


class PMOrchestrator:
    """PM Orchestrator - task execution, result processing, and auto-retry."""

    def __init__(
        self,
        stream_manager: RedisStreamManager,
        task_runner: LocalTaskRunner,
        state_machine: TaskStateMachine,
    ) -> None:
        self.stream_manager = stream_manager
        self.task_runner = task_runner
        self.state_machine = state_machine
        self._running = False

    _MAX_RETRIES = 5
    _BASE_BACKOFF = 2.0  # seconds

    async def start(self, project_id: uuid.UUID, db_session_factory: SessionFactory) -> None:
        """Start orchestration for a project with exponential backoff on crash."""
        self._running = True
        attempt = 0

        while self._running and attempt <= self._MAX_RETRIES:
            try:
                logger.info(
                    "Orchestrator starting for project %s (attempt %d/%d)",
                    project_id,
                    attempt + 1,
                    self._MAX_RETRIES + 1,
                )
                await self._promote_waiting_tasks(project_id, db_session_factory)
                await self._recover_orphaned_redesign_tasks(project_id, db_session_factory)
                logger.info("Orchestrator entering main loops for project %s", project_id)

                await asyncio.gather(
                    self._execution_loop(project_id, db_session_factory),
                    self._escalation_loop(project_id, db_session_factory),
                )
                # Clean exit (e.g. _running set to False)
                break

            except Exception:
                attempt += 1
                if attempt > self._MAX_RETRIES or not self._running:
                    logger.exception(
                        "Orchestrator permanently failed for project %s after %d attempts",
                        project_id,
                        attempt,
                    )
                    raise

                delay = self._BASE_BACKOFF * (2 ** (attempt - 1))
                logger.warning(
                    "Orchestrator crashed for project %s, retrying in %.1fs (attempt %d/%d)",
                    project_id,
                    delay,
                    attempt,
                    self._MAX_RETRIES,
                    exc_info=True,
                )
                await asyncio.sleep(delay)

            finally:
                logger.info("Orchestrator stopped for project %s", project_id)

    async def _promote_waiting_tasks(self, project_id: uuid.UUID, db_session_factory: SessionFactory) -> None:
        """Promote WAITING tasks in the active phase whose dependencies are all met to READY."""
        try:
            async with db_session_factory() as db:
                await self._promote_waiting_tasks_inner(project_id, db)
                await db.commit()
        except Exception:
            logger.exception("Promote waiting tasks error")

    async def _recover_orphaned_redesign_tasks(self, project_id: uuid.UUID, db_session_factory: SessionFactory) -> None:
        """Re-publish escalation messages for tasks stuck in redesign status."""
        try:
            async with db_session_factory() as db:
                repo = TaskRepository(db)
                redesign_tasks = await repo.list_by_project(project_id, status=TaskStatus.redesign)
                for task in redesign_tasks:
                    intervention_key = f"task:{task.id}:needs_intervention"
                    needs_intervention = await self.stream_manager.redis.get(intervention_key)
                    if needs_intervention:
                        logger.debug("Skipping intervention-flagged redesign task %s", task.id)
                        continue
                    if task.error_message and task.error_message.startswith("Redesign failed:"):
                        logger.debug("Skipping redesign-failed task %s (DB marker)", task.id)
                        await self.stream_manager.redis.set(intervention_key, "1", ex=_INTERVENTION_KEY_TTL)
                        continue
                    recovery_key = f"task:{task.id}:recovery_count"
                    recovery_count = int(await self.stream_manager.redis.get(recovery_key) or 0)
                    if recovery_count >= 3:
                        logger.warning(
                            "Redesign task %s recovered %d times without resolution, flagging for intervention",
                            task.id,
                            recovery_count,
                        )
                        await self.stream_manager.redis.set(intervention_key, "1", ex=_INTERVENTION_KEY_TTL)
                        continue
                    await self.stream_manager.redis.set(recovery_key, str(recovery_count + 1), ex=_RECOVERY_KEY_TTL)

                    logger.info("Recovering orphaned redesign task %s: %s", task.id, task.title)
                    await self.stream_manager.publish(
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
        except Exception:
            logger.exception("Recover orphaned redesign tasks error")

    async def _promote_waiting_tasks_inner(self, project_id: uuid.UUID, db: AsyncSession) -> None:
        """Promote WAITING tasks in the active phase (uses existing db session)."""
        phase_repo = PhaseRepository(db)
        task_repo = TaskRepository(db)

        active_phase = await phase_repo.get_active_phase(project_id)
        if active_phase is None:
            first_pending = await phase_repo.get_first_pending_phase(project_id)
            if first_pending is None:
                return
            first_pending.status = PhaseStatus.active
            active_phase = first_pending

        waiting_tasks = await task_repo.list_waiting_in_phase(active_phase.id)
        for task in waiting_tasks:
            if await task_repo.check_dependencies_met(task.id):
                await self.state_machine.transition(
                    task=task,
                    new_status=TaskStatus.ready,
                    reason="All dependencies met",
                    actor="system",
                    db_session=db,
                    stream_manager=self.stream_manager,
                )

    async def stop(self) -> None:
        """Stop orchestration."""
        self._running = False

    # ── Execution Loop ─────────────────────────────────────────────────

    async def _execution_loop(self, project_id: uuid.UUID, db_session_factory: SessionFactory) -> None:
        """Execute tasks with configurable concurrency per project."""
        logger.info("Execution loop started for project %s", project_id)
        redesign_check_counter = 0
        concurrent_tasks: set[asyncio.Task[None]] = set()

        while self._running:
            try:
                task_to_execute = None
                repo_path = ""
                max_concurrent = 1  # default

                async with db_session_factory() as db:
                    repo = TaskRepository(db)

                    # Get project's concurrency limit
                    project_repo = ProjectRepository(db)
                    project = await project_repo.get_by_id(project_id, load_phases=False)
                    if project:
                        max_concurrent = getattr(project, "max_concurrent_tasks", 1) or 1
                        repo_path = project.workspace_dir or project.repo_path or ""

                    # Concurrency constraint: respect max_concurrent_tasks
                    active_count = await repo.count_active_tasks(project_id)
                    if active_count >= max_concurrent:
                        await db.commit()
                    else:
                        # Check phase completion and advance
                        phase_events = await self._check_and_advance_phase(project_id, db)

                        # Promote waiting tasks in the active phase
                        await self._promote_waiting_tasks_inner(project_id, db)

                        ready_tasks = await repo.list_ready_by_priority(project_id)

                        if ready_tasks:
                            task_to_execute = ready_tasks[0]
                            # Transition: ready → in_progress
                            await self.state_machine.transition(
                                task=task_to_execute,
                                new_status=TaskStatus.in_progress,
                                reason="Scheduled by PM",
                                actor="pm",
                                db_session=db,
                                stream_manager=self.stream_manager,
                            )

                        await db.commit()
                        if task_to_execute is not None:
                            db.expunge(task_to_execute)

                        # Publish phase events after successful commit
                        for event_type, event_data in phase_events:
                            await self.stream_manager.publish_board_event(event_type, event_data)

                # Execute task outside DB transaction (long-running)
                if task_to_execute is not None:
                    if max_concurrent <= 1:
                        # Sequential mode (backward compatible)
                        await self._execute_and_review(task_to_execute, repo_path, project_id, db_session_factory)
                    else:
                        # Concurrent mode: spawn as asyncio task
                        atask = asyncio.create_task(
                            self._execute_and_review(task_to_execute, repo_path, project_id, db_session_factory)
                        )
                        concurrent_tasks.add(atask)
                        atask.add_done_callback(concurrent_tasks.discard)

                # Clean up completed concurrent tasks
                concurrent_tasks = {t for t in concurrent_tasks if not t.done()}

            except Exception:
                logger.exception("Execution loop error")

            # Periodically re-publish orphaned redesign tasks
            redesign_check_counter += 1
            if redesign_check_counter >= 6:
                redesign_check_counter = 0
                await self._recover_orphaned_redesign_tasks(project_id, db_session_factory)

            if task_to_execute is None:
                await asyncio.sleep(5)

    async def _execute_and_review(
        self,
        task: Task,
        repo_path: str,
        project_id: uuid.UUID,
        db_session_factory: SessionFactory,
    ) -> None:
        """Execute a task and run QA review inline."""
        task_id = task.id

        # Step 1: Execute the task
        exec_result = await self.task_runner.execute_task(task, repo_path)

        async with db_session_factory() as db:
            repo = TaskRepository(db)
            task = await repo.get_by_id(task_id)
            if not task:
                logger.warning("Task %s disappeared during execution", task_id)
                return

            if not exec_result.success:
                await self._handle_execution_failure(
                    task,
                    db,
                    exec_result.error_message,
                    exec_result.error_category,
                )
                await db.commit()
                return

            # Step 2: Transition to review
            await self.state_machine.transition(
                task=task,
                new_status=TaskStatus.review,
                reason="Execution succeeded, starting QA",
                actor="pm",
                db_session=db,
                stream_manager=self.stream_manager,
            )
            await db.commit()
            db.expunge(task)

        # Step 3: Run QA review (outside DB transaction)
        review_result = await self.task_runner.review_task(task, repo_path)

        async with db_session_factory() as db:
            repo = TaskRepository(db)
            task = await repo.get_by_id(task_id)
            if not task:
                logger.warning("Task %s disappeared during QA", task_id)
                return

            if review_result.passed:
                if review_result.commit_hash:
                    task.commit_hash = review_result.commit_hash
                await self.state_machine.transition(
                    task=task,
                    new_status=TaskStatus.done,
                    reason="QA passed",
                    actor="pm",
                    db_session=db,
                    stream_manager=self.stream_manager,
                )
            else:
                await self._handle_qa_failure(
                    task,
                    db,
                    feedback=review_result.feedback,
                    error_message=review_result.error_message,
                    error_category=review_result.error_category,
                )
            await db.commit()

    async def _check_and_advance_phase(
        self, project_id: uuid.UUID, db: AsyncSession
    ) -> list[tuple[str, dict[str, str]]]:
        """Check if active phase is complete and advance to the next phase."""
        events: list[tuple[str, dict[str, str]]] = []

        phase_repo = PhaseRepository(db)
        active_phase = await phase_repo.get_active_phase(project_id)
        if active_phase is None:
            return events

        incomplete = await phase_repo.count_incomplete_tasks(active_phase.id)
        if incomplete > 0:
            return events

        # Phase completed
        active_phase.status = PhaseStatus.completed
        events.append(
            (
                "phase_completed",
                {
                    "phase_id": str(active_phase.id),
                    "project_id": str(project_id),
                    "phase_name": active_phase.name,
                    "phase_order": str(active_phase.order),
                },
            )
        )

        # Activate next phase
        next_phase = await phase_repo.get_next_pending_phase(project_id, active_phase.order)
        if next_phase is not None:
            next_phase.status = PhaseStatus.active
            events.append(
                (
                    "phase_activated",
                    {
                        "phase_id": str(next_phase.id),
                        "project_id": str(project_id),
                        "phase_name": next_phase.name,
                        "phase_order": str(next_phase.order),
                    },
                )
            )

        return events

    # ── Failure Handling ───────────────────────────────────────────────

    async def _handle_execution_failure(
        self, task: Task, db: AsyncSession, error_msg: str, error_category: str = ""
    ) -> None:
        """Handle execution failure with auto-retry or escalation to redesign."""
        original_count = task.retry_count
        original_history = copy.deepcopy(task.qa_feedback_history) if task.qa_feedback_history else None
        try:
            task.retry_count += 1

            if task.qa_feedback_history is None:
                task.qa_feedback_history = []
            task.qa_feedback_history.append(
                {
                    "type": "execution_failure",
                    "attempt": task.retry_count,
                    "error": error_msg,
                    "error_category": error_category,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )

            if task.retry_count > task.max_retries:
                await self.state_machine.transition(
                    task=task,
                    new_status=TaskStatus.redesign,
                    reason=f"Max retries ({task.max_retries}) exceeded. Last error: {error_msg}",
                    actor="pm",
                    db_session=db,
                    stream_manager=self.stream_manager,
                )
                await self._create_bug_task(task, db, error_msg, error_category)
            else:
                await self.state_machine.transition(
                    task=task,
                    new_status=TaskStatus.ready,
                    reason=f"Execution failed (attempt {task.retry_count}/{task.max_retries}): {error_msg}",
                    actor="pm",
                    db_session=db,
                    stream_manager=self.stream_manager,
                )
        except Exception:
            task.retry_count = original_count
            task.qa_feedback_history = original_history
            raise

    async def _handle_qa_failure(
        self,
        task: Task,
        db: AsyncSession,
        feedback: str = "",
        error_message: str = "",
        error_category: str = "",
    ) -> None:
        """Handle QA failure with auto-retry or escalation to redesign."""
        original_count = task.retry_count
        original_history = copy.deepcopy(task.qa_feedback_history) if task.qa_feedback_history else None
        try:
            task.retry_count += 1
            effective_feedback = feedback or error_message

            if task.qa_feedback_history is None:
                task.qa_feedback_history = []
            task.qa_feedback_history.append(
                {
                    "type": "qa_failure",
                    "attempt": task.retry_count,
                    "feedback": effective_feedback,
                    "error_category": error_category,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )

            if task.retry_count > task.max_retries:
                await self.state_machine.transition(
                    task=task,
                    new_status=TaskStatus.redesign,
                    reason=f"Max retries ({task.max_retries}) exceeded. Last QA feedback: {effective_feedback}",
                    actor="pm",
                    db_session=db,
                    stream_manager=self.stream_manager,
                )
                await self._create_bug_task(task, db, effective_feedback, error_category)
            else:
                # Retry: back to ready (will be re-executed with feedback in qa_feedback_history)
                await self.state_machine.transition(
                    task=task,
                    new_status=TaskStatus.ready,
                    reason=f"QA failed (attempt {task.retry_count}/{task.max_retries}), auto-retrying",
                    actor="pm",
                    db_session=db,
                    stream_manager=self.stream_manager,
                )
        except Exception:
            task.retry_count = original_count
            task.qa_feedback_history = original_history
            raise

    async def _create_bug_task(
        self, failed_task: Task, db: AsyncSession, error_msg: str, error_category: str = ""
    ) -> Task | None:
        """Create a bug task linked to a failed task for explicit tracking."""
        try:
            failure_history = json.dumps(failed_task.qa_feedback_history or [], indent=2)
            description = (
                f"Bug: {failed_task.title} failed after {failed_task.retry_count} attempts.\n\n"
                f"Error: {error_msg}\n"
                f"Category: {error_category or 'unknown'}\n\n"
                f"Failure History:\n{failure_history}"
            )

            bug_task = Task(
                project_id=failed_task.project_id,
                phase_id=failed_task.phase_id,
                title=f"Bug: {failed_task.title}",
                description=description,
                priority=TaskPriority.critical,
                task_type=TaskType.bug,
                source=TaskSource.auto_bug,
                parent_task_id=failed_task.id,
                status=TaskStatus.ready,
                worker_prompt={
                    "prompt": (
                        f"Fix the bug in task '{failed_task.title}'.\n\n"
                        f"Original error: {error_msg}\n\n"
                        f"Review the failure history and fix the root cause:\n{failure_history}"
                    )
                },
                qa_prompt={
                    "prompt": (
                        f"Verify that the bug from '{failed_task.title}' is fixed.\n"
                        "Run regression tests to ensure the fix doesn't break other functionality."
                    )
                },
                branch_name=failed_task.branch_name,
            )
            db.add(bug_task)
            await db.flush()

            if self.stream_manager:
                await self.stream_manager.publish_board_event(
                    "bug_task_created",
                    {
                        "project_id": str(failed_task.project_id),
                        "task_id": str(bug_task.id),
                        "parent_task_id": str(failed_task.id),
                        "title": bug_task.title,
                    },
                )

            logger.info("Bug task created bug_task_id=%s parent_task_id=%s", bug_task.id, failed_task.id)
            return bug_task
        except Exception:
            logger.exception("Failed to create bug task for %s", failed_task.id)
            return None

    # ── Escalation Loop (Auto-Redesign) ────────────────────────────────

    async def _escalation_loop(self, project_id: uuid.UUID, db_session_factory: SessionFactory) -> None:
        """Consume escalation messages and auto-redesign tasks via Architect LLM."""
        logger.info("Escalation loop started for project %s", project_id)
        while self._running:
            try:
                messages = await self.stream_manager.consume(
                    stream=RedisStreamManager.TASKS_ESCALATION,
                    group=RedisStreamManager.GROUP_ARCHITECT,
                    consumer="architect-0",
                    count=1,
                    block=5000,
                )
                for msg in messages:
                    task_id = msg.get("task_id", "?")
                    msg_id = msg.get("_message_id", "?")
                    try:
                        # Handle heartbeat events: acknowledge and log
                        event_type = msg.get("event", "")
                        if event_type in ("agent_heartbeat", "agent_heartbeat_immediate"):
                            agent_id = msg.get("agent_id", "?")
                            logger.info(
                                "escalation_heartbeat_received",
                                agent_id=agent_id,
                                event=event_type,
                            )
                            await self.stream_manager.acknowledge(
                                RedisStreamManager.TASKS_ESCALATION,
                                RedisStreamManager.GROUP_ARCHITECT,
                                msg_id,
                            )
                            continue

                        msg_project_id = msg.get("project_id", "")
                        if str(project_id) != str(msg_project_id):
                            logger.debug(
                                "Escalation: skipping msg %s (project %s != %s)", msg_id, msg_project_id, project_id
                            )
                            await self.stream_manager.acknowledge(
                                RedisStreamManager.TASKS_ESCALATION,
                                RedisStreamManager.GROUP_ARCHITECT,
                                msg_id,
                            )
                            continue

                        logger.info("Escalation: processing task %s (msg %s)", task_id, msg_id)
                        async with db_session_factory() as db:
                            await self._process_escalation(msg, db)
                            await db.commit()
                        logger.info("Escalation: completed task %s (msg %s)", task_id, msg_id)
                        await self.stream_manager.acknowledge(
                            RedisStreamManager.TASKS_ESCALATION,
                            RedisStreamManager.GROUP_ARCHITECT,
                            msg_id,
                        )
                    except Exception:
                        logger.exception("Escalation processing error for task %s (msg %s)", task_id, msg_id)
                        try:
                            await self.stream_manager.acknowledge(
                                RedisStreamManager.TASKS_ESCALATION,
                                RedisStreamManager.GROUP_ARCHITECT,
                                msg_id,
                            )
                        except Exception:
                            logger.exception("Failed to ACK escalation message after error")
            except Exception:
                if self._running:
                    logger.exception("Escalation loop error")
                    await asyncio.sleep(5)

    async def _mark_redesign_needs_intervention(self, task: Task, reason: str, db: AsyncSession) -> None:
        """Flag task as needing manual intervention."""
        task.error_message = f"Redesign failed: {reason}"
        intervention_key = f"task:{task.id}:needs_intervention"
        await self.stream_manager.redis.set(intervention_key, "1", ex=_INTERVENTION_KEY_TTL)

        await self.stream_manager.publish_board_event(
            "auto_redesign_failed",
            {
                "task_id": str(task.id),
                "project_id": str(task.project_id),
                "reason": reason,
            },
        )

    async def _process_escalation(self, msg: dict[str, Any], db: AsyncSession) -> None:
        """Process a single escalation message: phase-level redesign via Architect LLM."""
        task_id = msg.get("task_id", "")
        try:
            tid = uuid.UUID(task_id) if isinstance(task_id, str) else task_id
        except ValueError:
            logger.warning("Escalation: invalid task_id %r, skipping", task_id)
            return
        task_repo = TaskRepository(db)
        task = await task_repo.get_by_id(tid)
        if not task:
            logger.warning("Escalation: task %s not found, skipping", task_id)
            return
        if task.status != TaskStatus.redesign:
            logger.info("Escalation: task %s not in redesign status (%s), skipping", task_id, task.status.value)
            return

        # Detect environment errors
        last_category = ""
        if task.qa_feedback_history:
            last_entry = task.qa_feedback_history[-1]
            last_category = last_entry.get("error_category", "") if isinstance(last_entry, dict) else ""
        if last_category == "environment":
            logger.info("Escalation: task %s has environment error, needs intervention", task_id)
            await self._mark_redesign_needs_intervention(
                task,
                f"Environment error (not fixable by redesign): {task.error_message}",
                db,
            )
            return

        # Check auto-redesign limit
        redis_key = f"phase:{task.phase_id}:auto_redesign_count"
        count_raw = await self.stream_manager.redis.get(redis_key)
        auto_redesign_count = int(count_raw) if count_raw else 0

        if auto_redesign_count >= settings.max_auto_redesigns:
            logger.info(
                "Escalation: phase %s reached max auto-redesigns (%d), needs intervention",
                task.phase_id,
                auto_redesign_count,
            )
            await self._mark_redesign_needs_intervention(
                task,
                f"Auto-redesign limit ({settings.max_auto_redesigns}) reached",
                db,
            )
            return

        # Load project for LLM config
        project_repo = ProjectRepository(db)
        project = await project_repo.get_by_id(task.project_id, load_phases=False)
        if not project:
            logger.warning("Escalation: project %s not found for task %s", task.project_id, task_id)
            await self._mark_redesign_needs_intervention(task, "Project not found", db)
            return

        try:
            llm_client = create_llm_client_from_project(project, role="architect")
        except ValueError as e:
            logger.warning("Escalation: no LLM config for project %s: %s", project.id, e)
            await self._mark_redesign_needs_intervention(task, f"No architect LLM configuration: {e}", db)
            return

        phase_repo = PhaseRepository(db)
        phase = await phase_repo.get_by_id(task.phase_id)
        if not phase:
            logger.warning("Escalation: phase %s not found for task %s", task.phase_id, task_id)
            await self._mark_redesign_needs_intervention(task, "Phase not found", db)
            return

        incomplete_tasks = await task_repo.list_incomplete_in_phase(task.phase_id)
        done_tasks = await task_repo.list_done_in_phase(task.phase_id)

        def _task_to_dict(t: Task) -> dict[str, Any]:
            wp = ""
            if t.worker_prompt:
                wp = (
                    t.worker_prompt.get("prompt", json.dumps(t.worker_prompt))
                    if isinstance(t.worker_prompt, dict)
                    else str(t.worker_prompt)
                )
            qp = ""
            if t.qa_prompt:
                qp = (
                    t.qa_prompt.get("prompt", json.dumps(t.qa_prompt))
                    if isinstance(t.qa_prompt, dict)
                    else str(t.qa_prompt)
                )
            dep_ids = [str(d.id) for d in t.depends_on] if t.depends_on else []
            return {
                "id": str(t.id),
                "title": t.title,
                "description": t.description or "",
                "priority": t.priority.value,
                "status": t.status.value,
                "worker_prompt": wp,
                "qa_prompt": qp,
                "depends_on": dep_ids,
                "retry_count": t.retry_count,
                "error_message": t.error_message or "",
            }

        incomplete_dicts = [_task_to_dict(t) for t in incomplete_tasks]
        done_dicts = [{"id": str(t.id), "title": t.title, "status": "done"} for t in done_tasks]

        failure_history = json.dumps(task.qa_feedback_history or [], indent=2)

        prompt = get_prompt("architect", "phase_redesign").format(
            failed_task_title=task.title,
            failed_task_error=task.error_message or "No error message",
            failed_task_history=failure_history,
            done_tasks=json.dumps(done_dicts, indent=2, ensure_ascii=False),
            incomplete_tasks=json.dumps(incomplete_dicts, indent=2, ensure_ascii=False),
            phase_name=phase.name,
            branch_name=phase.branch_name or "N/A",
        )

        llm_messages = [
            {"role": "system", "content": get_prompt("architect", "system")},
            {"role": "user", "content": prompt},
        ]

        try:
            result = await llm_client.structured_output(
                messages=llm_messages,
                response_format={"type": "json_object"},
            )
        except LLMError as e:
            logger.error("Escalation: LLM call failed for task %s: %s", task_id, e)
            await self._mark_redesign_needs_intervention(task, f"LLM error: {e}", db)
            return

        reasoning = result.get("reasoning", "")
        new_task_list = result.get("tasks", [])

        if not isinstance(new_task_list, list):
            logger.error("Escalation: LLM returned invalid tasks format for phase %s", task.phase_id)
            await self._mark_redesign_needs_intervention(task, "LLM returned invalid tasks format", db)
            return

        try:
            await self._apply_phase_redesign(
                phase=phase,
                incomplete_tasks=incomplete_tasks,
                new_task_list=new_task_list,
                reasoning=reasoning,
                task_repo=task_repo,
                db=db,
            )
        except Exception as e:
            logger.exception("Escalation: failed to apply phase redesign for phase %s", task.phase_id)
            await self._mark_redesign_needs_intervention(task, f"Failed to apply redesign: {e}", db)
            return

        await self.stream_manager.redis.incr(redis_key)
        await self.stream_manager.redis.expire(redis_key, _INTERVENTION_KEY_TTL)

        await self.stream_manager.publish_board_event(
            "auto_redesign_applied",
            {
                "task_id": str(task.id),
                "project_id": str(task.project_id),
                "phase_id": str(task.phase_id),
                "reasoning": reasoning,
            },
        )

        logger.info(
            "Phase-level auto-redesign applied for phase %s (triggered by task %s): %s",
            task.phase_id,
            task_id,
            reasoning,
        )

    async def _apply_phase_redesign(
        self,
        phase: Phase,
        incomplete_tasks: list[Task],
        new_task_list: list[dict[str, Any]],
        reasoning: str,
        task_repo: TaskRepository,
        db: AsyncSession,
    ) -> None:
        """Apply LLM redesign result by diffing against existing incomplete tasks."""
        existing_by_id: dict[str, Task] = {str(t.id): t for t in incomplete_tasks}
        result_ids: set[str] = set()
        new_tasks_data: list[dict[str, Any]] = []

        for item in new_task_list:
            item_id = item.get("id", "")
            if item_id and item_id in existing_by_id:
                result_ids.add(item_id)
            else:
                new_tasks_data.append(item)

        # Delete tasks not in the result
        to_delete = [t for tid, t in existing_by_id.items() if tid not in result_ids]
        if to_delete:
            delete_ids = [t.id for t in to_delete]
            logger.info("Phase redesign: deleting %d tasks: %s", len(delete_ids), [str(i) for i in delete_ids])
            await task_repo.hard_delete_many(delete_ids)

        # Update kept tasks
        for item in new_task_list:
            item_id = item.get("id", "")
            if not item_id or item_id not in existing_by_id:
                continue
            task = existing_by_id[item_id]
            if item.get("title"):
                task.title = item["title"]
            if item.get("description"):
                task.description = item["description"]
            if item.get("worker_prompt"):
                task.worker_prompt = {"prompt": item["worker_prompt"]}
            if item.get("qa_prompt"):
                task.qa_prompt = {"prompt": item["qa_prompt"]}
            if item.get("priority"):
                try:
                    task.priority = TaskPriority(item["priority"])
                except ValueError:
                    pass
            task.retry_count = 0
            task.qa_feedback_history = None
            task.error_message = None
            task.commit_hash = None
            task.started_at = None
            if task.status != TaskStatus.waiting:
                await self.state_machine.transition(
                    task=task,
                    new_status=TaskStatus.waiting,
                    reason=f"Phase redesign: {reasoning}",
                    actor="architect",
                    db_session=db,
                    stream_manager=self.stream_manager,
                )

        # Create new tasks
        created_tasks: dict[str, Task] = {}
        for item in new_tasks_data:
            priority_str = item.get("priority", "medium")
            try:
                priority = TaskPriority(priority_str)
            except ValueError:
                priority = TaskPriority.medium

            new_task = Task(
                project_id=phase.project_id,
                phase_id=phase.id,
                title=item.get("title", "Untitled Task"),
                description=item.get("description"),
                priority=priority,
                status=TaskStatus.waiting,
                worker_prompt={"prompt": item.get("worker_prompt", "")},
                qa_prompt={"prompt": item.get("qa_prompt", "")},
                branch_name=phase.branch_name,
            )
            db.add(new_task)
            await db.flush()
            created_tasks[new_task.title] = new_task

        # Wire up dependencies
        all_tasks_by_id: dict[str, Task] = {}
        all_tasks_by_title: dict[str, Task] = {}
        for tid, t in existing_by_id.items():
            if tid in result_ids:
                all_tasks_by_id[tid] = t
                all_tasks_by_title[t.title] = t
        for title, t in created_tasks.items():
            all_tasks_by_id[str(t.id)] = t
            all_tasks_by_title[title] = t

        for item in new_task_list:
            item_id = item.get("id", "")
            item_title = item.get("title", "")
            task = all_tasks_by_id.get(item_id) or all_tasks_by_title.get(item_title)
            if not task:
                continue

            depends_on_refs = item.get("depends_on", [])
            if depends_on_refs:
                await task_repo.clear_dependencies(task.id)
                dep_ids: list[uuid.UUID] = []
                for ref in depends_on_refs:
                    dep_task = all_tasks_by_id.get(ref) or all_tasks_by_title.get(ref)
                    if dep_task:
                        dep_ids.append(dep_task.id)
                if dep_ids:
                    await task_repo.add_dependencies(task.id, dep_ids)

    async def queue_next(self, project_id: uuid.UUID, db: AsyncSession) -> Task | None:
        """Manually promote the next waiting task to ready so the execution loop picks it up.

        Returns the promoted task, or the first ready task if one already exists,
        or None if nothing is available.
        """
        repo = TaskRepository(db)

        # If a task is already running, nothing to do
        active_count = await repo.count_active_tasks(project_id)
        if active_count > 0:
            return None

        # If there's already a ready task, just return it (execution loop will pick it up)
        ready_tasks = await repo.list_ready_by_priority(project_id)
        if ready_tasks:
            return ready_tasks[0]

        # Promote waiting tasks whose dependencies are met
        await self._promote_waiting_tasks_inner(project_id, db)
        ready_tasks = await repo.list_ready_by_priority(project_id)
        if ready_tasks:
            return ready_tasks[0]

        return None
