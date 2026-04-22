"""Global background dispatcher loop.

A single asyncio task started in the FastAPI lifespan that periodically:

1. Promotes pending tasks whose dependencies are met into running state by
   handing them to an available worker.
2. Advances phases: when every task in the active phase is done, marks
   the phase complete and activates the next pending phase.
3. Publishes plan SSE events so the frontend updates without polling.

This replaces the old per-project PMOrchestrator. There is one loop per
process, not one per project — the chat path stays fire-and-forget and
this loop catches everything else (manual task creation, server restart,
worker reconnect, dependency promotion).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.state_machine import TaskStateMachine
from backend.src.core.worker_dispatch import WorkerDispatcher
from backend.src.models import (
    Agent,
    Phase,
    PhaseStatus,
    Project,
    ProjectStatus,
    Task,
    TaskStatus,
)
from backend.src.queue.streams import RedisStreamManager
from backend.src.repositories.phase_repository import PhaseRepository
from backend.src.repositories.task_repository import TaskRepository
from backend.src.storage.database import async_session

logger = structlog.get_logger(__name__)

DISPATCH_INTERVAL_SECONDS = 5.0


class GlobalDispatcher:
    """Single-instance background dispatcher.

    The instance lives on ``app.state.global_dispatcher`` and is started /
    stopped from the FastAPI lifespan.
    """

    def __init__(self, stream_manager: RedisStreamManager) -> None:
        self._stream = stream_manager
        self._state_machine = TaskStateMachine()
        self._worker_dispatcher = WorkerDispatcher(stream_manager)
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        # Per-project pause: stop-all sets this, restart clears it.
        self._paused_projects: set[uuid.UUID] = set()

    def pause_project(self, project_id: uuid.UUID) -> None:
        """Pause dispatch for a project (stop-all)."""
        self._paused_projects.add(project_id)
        logger.info("dispatcher_project_paused", project_id=str(project_id))

    def resume_project(self, project_id: uuid.UUID) -> None:
        """Resume dispatch for a project (restart)."""
        self._paused_projects.discard(project_id)
        logger.info("dispatcher_project_resumed", project_id=str(project_id))

    # ── lifecycle ────────────────────────────────────────────────

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="global-dispatcher")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is None:
            return
        try:
            await asyncio.wait_for(self._task, timeout=5.0)
        except asyncio.TimeoutError:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, BaseException):  # noqa: BLE001
                pass
        self._task = None

    # ── main loop ────────────────────────────────────────────────

    async def _run(self) -> None:
        logger.info("global_dispatcher_started", interval=DISPATCH_INTERVAL_SECONDS)
        while not self._stop_event.is_set():
            try:
                await self.tick()
            except Exception:  # noqa: BLE001
                logger.exception("global_dispatcher_tick_failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=DISPATCH_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                continue
        logger.info("global_dispatcher_stopped")

    async def tick(self) -> None:
        """One iteration of the dispatcher loop. Public for tests."""
        async with async_session() as db:
            active_projects = await self._list_active_projects(db)
            for project in active_projects:
                if project.id in self._paused_projects:
                    continue
                await self._advance_phase_if_complete(db, project.id)
                await self._promote_and_dispatch(db, project.id)
                await self._reassign_orphaned_tasks(db, project.id)
                await self._dispatch_agent_tasks(db, project.id)
            await db.commit()

    # ── helpers ─────────────────────────────────────────────────

    async def _list_active_projects(self, db: AsyncSession) -> list[Project]:
        result = await db.execute(
            select(Project).where(
                Project.status.in_([ProjectStatus.active, ProjectStatus.design])
            )
        )
        return list(result.scalars().all())

    async def _promote_and_dispatch(self, db: AsyncSession, project_id: uuid.UUID) -> None:
        """Find pending tasks in the active phase and hand them to workers."""
        phase_repo = PhaseRepository(db)
        active_phase = await phase_repo.get_active_phase(project_id)
        if active_phase is None:
            return

        task_repo = TaskRepository(db)
        pending_tasks = await task_repo.list_ready_by_priority(project_id)
        for task in pending_tasks:
            if task.phase_id != active_phase.id:
                continue
            if not await task_repo.check_dependencies_met(task.id):
                continue

            worker = await self._worker_dispatcher.find_available_worker(db)
            if worker is None:
                # No capacity right now — try again next tick.
                return

            try:
                await self._state_machine.transition(
                    task=task,
                    new_status=TaskStatus.running,
                    reason="dispatched by global dispatcher",
                    actor="dispatcher",
                    db_session=db,
                    stream_manager=self._stream,
                )
            except ValueError:
                # Already running or otherwise not eligible — skip.
                continue

            await self._worker_dispatcher.dispatch_task(
                worker_id=worker.id,
                task_id=task.id,
                task_title=task.title,
                project_id=str(project_id),
                prompt=_extract_prompt(task.worker_prompt),
            )

    async def _reassign_orphaned_tasks(self, db: AsyncSession, project_id: uuid.UUID) -> None:
        """Auto-assign orphaned tasks (assigned_agent_id IS NULL) via keyword matching."""
        from backend.src.core.task_assignment import match_agent_for_task
        from backend.src.models import Agent

        result = await db.execute(
            select(Task).where(
                Task.project_id == project_id,
                Task.status == TaskStatus.pending,
                Task.assigned_agent_id.is_(None),
                Task.source == "llm",
            ).order_by(Task.created_at.asc()).limit(5)
        )
        orphans = list(result.scalars().all())
        if not orphans:
            return

        agents_result = await db.execute(
            select(Agent).where(Agent.is_active.is_(True))
        )
        all_agents = list(agents_result.scalars().all())

        for task in orphans:
            text = f"{task.title} {task.description or ''}".lower()
            best = match_agent_for_task(text, all_agents, exclude_id=task.creator_agent_id)
            if best:
                task.assigned_agent_id = best.id
                logger.info("orphan_task_assigned", task_id=str(task.id),
                            title=task.title, agent=best.name)

    async def _advance_phase_if_complete(self, db: AsyncSession, project_id: uuid.UUID) -> None:
        """If every task in the active phase is done, activate the next pending phase.

        Guards against empty phases (0 tasks) being immediately marked complete.
        After phase advancement, auto-dispatches the org-root agent (CEO) in
        active mode so they can plan tasks for the next phase — this is the key
        mechanism that keeps the delegation chain going across phase boundaries.

        Completed projects are short-circuited here as a double-safety: the
        tick loop already filters them via ``_list_active_projects``, but a
        direct caller (tests, a future hook) still gets the guard.
        """
        project = await db.get(Project, project_id)
        if project is None or project.status == ProjectStatus.completed:
            return

        phase_repo = PhaseRepository(db)
        active_phase = await phase_repo.get_active_phase(project_id)
        if active_phase is None:
            return

        # Guard: a phase with zero tasks is not "complete" — it hasn't been
        # planned yet. Skip advancement until at least one task exists.
        total = await phase_repo.count_total_tasks(active_phase.id)
        if total == 0:
            return

        incomplete = await phase_repo.count_incomplete_tasks(active_phase.id)
        if incomplete > 0:
            return

        # Active phase is complete. Mark it done and activate the next one.
        active_phase.status = PhaseStatus.completed
        next_phase = await phase_repo.get_next_pending_phase(project_id, active_phase.order)
        if next_phase is not None:
            next_phase.status = PhaseStatus.active

        await self._stream.publish_project_event(
            str(project_id),
            "phase_advanced",
            {
                "completed_phase_id": str(active_phase.id),
                "next_phase_id": str(next_phase.id) if next_phase is not None else None,
            },
        )

        # Auto-dispatch org-root (CEO) to plan the next phase.
        # This ensures the delegation chain continues across phase boundaries:
        # Phase N tasks done → CEO plans Phase N+1 → CTO → Engineers.
        await self._auto_dispatch_phase_planning(db, project_id, active_phase, next_phase)

    async def _auto_dispatch_phase_planning(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
        completed_phase: Any,
        next_phase: Any | None,
    ) -> None:
        """Dispatch org-root agent to plan the next phase after completion."""
        # Double-safety: if the project got marked completed (via the
        # [PROJECT_COMPLETE] marker) between the phase transition above and
        # this call, stop chaining. Prevents CEO from being re-invoked after
        # the end-state signal has been emitted.
        project = await db.get(Project, project_id)
        if project is None or project.status == ProjectStatus.completed:
            logger.info(
                "phase_auto_chain_skipped_project_completed",
                project_id=str(project_id),
                completed_phase=completed_phase.name,
            )
            return

        agents_result = await db.execute(
            select(Agent).where(Agent.is_active.is_(True))
        )
        all_agents = list(agents_result.scalars().all())
        if not all_agents:
            return

        # Find org root (no parent = top of hierarchy)
        roots = [a for a in all_agents if not a.parent_agent_id]
        org_root = roots[0] if roots else None
        if not org_root:
            return

        if next_phase is not None:
            message = (
                f"'{completed_phase.name}' 단계가 완료되었습니다. "
                f"다음 단계 '{next_phase.name}'을 시작합니다. "
                f"list_tasks로 현재 상태를 확인하고, 이 단계에 필요한 작업을 만들어 "
                f"적절한 팀원에게 @mention으로 배분해주세요."
            )
        else:
            # All phases are done. Force a structured, *evaluated* choice.
            # Early naive "pick one of two" directive caused false-positive
            # PROJECT_COMPLETE — GLM took the easy escape hatch and
            # declared a project "done" with only the planning phase run.
            # Require a per-criterion checklist against the Goal BEFORE
            # allowing PROJECT_COMPLETE.
            message = (
                f"'{completed_phase.name}' 단계가 완료되었고 현재 열린 phase 중 "
                f"pending/active 가 없습니다. 그러나 프로젝트가 정말 끝났는지는 "
                f"'## Project Goal' 의 **완료 기준**이 실제로 충족됐는지에 달렸습니다.\n\n"
                f"### 1단계 — 체크리스트 평가 (MANDATORY)\n"
                f"'## Project Goal' 의 description 에서 완료 기준을 **한 줄씩** 추출해 "
                f"아래 형식으로 평가하세요. ✅ 판정의 **유일한 근거는 실제 workspace 파일 경로 + "
                f"바이트 크기** 입니다. 추정/희망/작업 이름은 모두 ❌ 처리합니다.\n\n"
                f"#### ✅ 판정 조건\n"
                f"- 산출물 유형별 허용 증거:\n"
                f"  - **소스코드/구현**: `src/...ts`, `backend/.../app.py`, `package.json` 등 "
                f"실제 코드/매니페스트 파일 경로 + 크기 (**최소 200바이트 이상**)\n"
                f"  - **디자인 화면**: `.bsd` 파일 **중 `spec` 에 실제 컴포넌트가 있거나 "
                f"`generated_code` 가 채워진 것만** (빈 stub `{{\"spec\":{{\"type\":\"Screen\"}}}}` 은 ❌)\n"
                f"  - **문서/스펙**: `docs/*.md` 경로 + 크기\n"
                f"- 근거 format: `- ✅ 기준 (file: path/to/x.ts [512B], path/to/y.bsd [800B])`\n"
                f"- 파일 경로를 댈 수 없으면 **반드시 ❌**. task 이름은 금지.\n"
                f"- workspace 에 해당 유형 파일이 존재하는지 모르겠으면 ❌ (file_read 또는 "
                f"Plan State 의 파일 목록으로 확인).\n\n"
                f"#### 예시 (올바른 형식)\n"
                f"```\n"
                f"- ✅ 시장 조사 완료 (file: docs/market-research.md [3200B])\n"
                f"- ❌ 동작하는 앱 소스코드 (workspace 에 .ts/.py 파일 없음, schema.sql 만 존재)\n"
                f"- ❌ 디자인 화면 3종 (4 .bsd 파일 모두 generated_code: null)\n"
                f"```\n\n"
                f"### 2단계 — 분기\n"
                f"**모든 항목이 파일-경로 근거로 ✅ 인 경우에만** 아래 한 줄:\n"
                f"```\n"
                f"[PROJECT_COMPLETE summary=\"각 기준의 실제 산출물 파일을 1-2문장으로 요약\"]\n"
                f"```\n"
                f"(주의: 백엔드가 workspace 를 재스캔해서 증거가 없으면 이 마커를 **거부**합니다. "
                f"거짓 ✅ 로 우회 불가.)\n\n"
                f"**하나라도 ❌ 가 있으면 반드시** 다음 phase 를 여세요 "
                f"(`[PROJECT_COMPLETE]` emit 금지):\n"
                f"```\n"
                f"[CREATE_PHASE name=\"...\" description=\"미충족 기준 X·Y·Z 를 채우는 작업 범위\"]\n"
                f"[CREATE_TASK title=\"...\" assignee=\"...\" priority=\"...\"]  (3-5개, 실제 "
                f"파일 산출물 생성 task 여야 함)\n"
                f"```\n\n"
                f"자연어로 '완료했습니다' / '종료하겠습니다' 만 답하면 시스템이 무시합니다."
            )

        from backend.src.core.agent_queue import AgentRequest, get_agent_queue_manager
        mgr = get_agent_queue_manager()
        await mgr.enqueue(AgentRequest(
            mode="active",
            project_id=project_id,
            agent_id=org_root.id,
            tenant_id=org_root.tenant_id,
            redis=self._stream.redis if self._stream else None,
            message=message,
        ))
        logger.info(
            "phase_auto_chain_dispatched",
            project_id=str(project_id),
            completed_phase=completed_phase.name,
            next_phase=next_phase.name if next_phase else None,
            org_root=org_root.name,
        )


    async def _dispatch_agent_tasks(self, db: AsyncSession, project_id: uuid.UUID) -> None:
        """Find pending tasks with assigned_agent_id and dispatch agents in passive mode."""
        from backend.src.models import Agent

        result = await db.execute(
            select(Task).where(
                Task.project_id == project_id,
                Task.status == TaskStatus.pending,
                Task.assigned_agent_id.isnot(None),
            ).order_by(Task.created_at.asc()).limit(3)
        )
        pending_tasks = list(result.scalars().all())

        for task in pending_tasks:
            # Validate agent is still active
            agent_result = await db.execute(
                select(Agent).where(
                    Agent.id == task.assigned_agent_id,
                    Agent.is_active.is_(True),
                )
            )
            agent = agent_result.scalar_one_or_none()
            if not agent:
                # Agent deleted/inactive → orphan the task for reassignment
                task.assigned_agent_id = None
                logger.warning("orphaned_task", task_id=str(task.id), reason="agent_inactive")
                continue

            # Check if agent is already running a task (avoid double-dispatch)
            running_result = await db.execute(
                select(func.count(Task.id)).where(
                    Task.assigned_agent_id == task.assigned_agent_id,
                    Task.status == TaskStatus.running,
                )
            )
            if running_result.scalar_one() > 0:
                continue  # Agent is busy

            # Build task context with sibling tasks + phase info so agent
            # can see the surrounding plan without needing file_read.
            phase_name = "Unknown"
            if task.phase_id:
                phase_row = await db.execute(
                    select(Phase).where(Phase.id == task.phase_id)
                )
                phase = phase_row.scalar_one_or_none()
                if phase:
                    phase_name = phase.name

                # Sibling tasks in the same phase (to avoid overlap)
                sib_rows = await db.execute(
                    select(Task).where(
                        Task.phase_id == task.phase_id,
                        Task.id != task.id,
                    ).order_by(Task.created_at.asc())
                )
                siblings = sib_rows.scalars().all()
                sibling_lines = []
                for s in siblings[:15]:  # cap at 15 to keep context small
                    status = s.status.value if s.status else "pending"
                    sibling_lines.append(f"  - [{status}] {s.title}")
                sibling_text = "\n".join(sibling_lines) if sibling_lines else "  (none)"
            else:
                sibling_text = "  (no phase)"

            task_context = (
                f"Task ID: {task.id}\n"
                f"Title: {task.title}\n"
                f"Description: {task.description or 'No description'}\n"
                f"Priority: {task.priority.value}\n"
                f"Type: {task.task_type.value}\n"
                f"Phase: {phase_name}\n"
                f"\n## Sibling tasks in this phase\n{sibling_text}\n"
                f"\nFocus on YOUR task only. Other tasks are handled by other agents."
            )

            # Mark task as running BEFORE dispatch to prevent double-dispatch
            sm = TaskStateMachine()
            await sm.transition(
                task=task,
                new_status=TaskStatus.running,
                reason=f"passive dispatch to {agent.name}",
                actor="dispatcher",
                db_session=db,
                stream_manager=self._stream,
            )
            await db.flush()

            from backend.src.core.agent_queue import AgentRequest, get_agent_queue_manager
            mgr = get_agent_queue_manager()

            # Publish processing state immediately so UI shows green dot
            redis = self._stream.redis if self._stream else None
            if redis:
                import json as _json
                from backend.src.queue.streams import RedisStreamManager
                stream = RedisStreamManager.chat_events_stream(str(project_id))
                activity = f"{task.title} 진행 중"
                await self._stream.publish(stream, {
                    "event": "agent_processing",
                    "data": {
                        "agent_id": str(agent.id),
                        "agent_name": agent.name,
                        "status": "started",
                        "mode": "passive",
                        "activity": activity,
                    },
                })
                await redis.set(
                    f"agent:processing:{project_id}:{agent.id}",
                    _json.dumps({"activity": activity}),
                    ex=600,
                )

            await mgr.enqueue(AgentRequest(
                mode="passive",
                project_id=project_id,
                agent_id=agent.id,
                tenant_id=agent.tenant_id,
                redis=redis,
                task_id=task.id,
                task_context=task_context,
            ))
            logger.info("passive_agent_dispatched", agent=agent.name, task_id=str(task.id), title=task.title)


def _extract_prompt(worker_prompt: dict | None) -> str | None:
    if not worker_prompt:
        return None
    if isinstance(worker_prompt, dict):
        prompt = worker_prompt.get("prompt")
        if isinstance(prompt, str):
            return prompt
    return None


# ── lifespan helpers ─────────────────────────────────────────────


async def start_global_dispatcher(app: Any) -> GlobalDispatcher:
    stream_manager: RedisStreamManager = app.state.stream_manager
    dispatcher = GlobalDispatcher(stream_manager)
    dispatcher.start()
    app.state.global_dispatcher = dispatcher
    return dispatcher


async def stop_global_dispatcher(app: Any) -> None:
    dispatcher: GlobalDispatcher | None = getattr(app.state, "global_dispatcher", None)
    if dispatcher is not None:
        await dispatcher.stop()
        app.state.global_dispatcher = None
