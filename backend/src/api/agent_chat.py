"""Unified Project Chat API — async multi-agent with SSE.

POST /chat is **fire-and-forget**: persists the user message, dispatches all
target agents as independent background tasks, and returns immediately.
Each agent works in parallel; responses arrive via SSE.

When no @mention is given, the org-chart root agent's worker executor is used
to pick the best agent (internal routing — not stored in chat).
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import structlog
from bsvibe_auth import BSVibeUser
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sse_starlette.sse import EventSourceResponse

from backend.src import models
from backend.src.api.settings import get_raw_llm_config
from backend.src.config import settings
from backend.src.core.task_markers import CREATE_TASK_RE, build_project_context, strip_action_markers
from backend.src.core.auth import Permission, require_permission
from backend.src.core.goal_alignment import GoalAlignmentService
from backend.src.core.llm_client import LLMClient, LLMConfig
from backend.src.core.tenant_context import get_tenant_id
from backend.src.core.worker_dispatch import WorkerDispatcher
from backend.src.queue.streams import RedisStreamManager
from backend.src.repositories.conversation_repository import ConversationRepository
from backend.src.repositories.phase_repository import PhaseRepository
from backend.src.storage.database import async_session, get_db

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/projects/{project_id}/chat", tags=["agent-chat"])

SET_GOAL_RE = re.compile(r"\[SET_GOAL\](.*?)\[/SET_GOAL\]", re.DOTALL)
MAX_HISTORY = 100
WORKER_RESULT_TIMEOUT = 120.0
ROUTING_TIMEOUT = 30.0


# ── Schemas ─────────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=10000)


class ChatMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: str
    content: str
    agent_id: uuid.UUID | None = None
    agent_name: str | None = None
    created_at: Any
    actions: list[dict[str, Any]] = Field(default_factory=list)


class ChatDispatchResponse(BaseModel):
    """Returned immediately by POST /chat. Agent responses arrive via SSE."""
    dispatched_agents: list[str]


class ChatHistoryResponse(BaseModel):
    messages: list[ChatMessageOut]


# ── Mention parsing & agent routing ─────────────────────────────────


def _parse_mentions(message: str, agents: list[models.Agent]) -> list[models.Agent]:
    """Find @AgentName mentions in text. Returns matched agents in order of appearance."""
    mentioned: list[models.Agent] = []
    seen: set[uuid.UUID] = set()
    sorted_agents = sorted(agents, key=lambda a: len(a.name), reverse=True)
    lower_msg = message.lower()
    for agent in sorted_agents:
        if agent.id in seen:
            continue
        if f"@{agent.name.lower()}" in lower_msg:
            mentioned.append(agent)
            seen.add(agent.id)
    if len(mentioned) > 1:
        mentioned.sort(key=lambda a: lower_msg.index(f"@{a.name.lower()}"))
    return mentioned


def _find_org_root(agents: list[models.Agent]) -> models.Agent | None:
    """Top-level agent (no parent) — used as final fallback."""
    if not agents:
        return None
    roots = [a for a in agents if not a.parent_agent_id]
    return roots[0] if roots else agents[0]


async def _route_via_worker(
    message: str,
    agents: list[models.Agent],
    recent_history: list[models.ConversationMessage],
    db: AsyncSession,
    redis: Any,
) -> models.Agent | None:
    """Use the org-chart root's worker to pick the best agent.

    Internal infrastructure call — NOT stored in chat, NOT published to SSE.
    Falls back to org root if the worker call fails.
    """
    root = _find_org_root(agents)
    if not root or root.executor_type != "worker" or redis is None:
        return root

    agent_list = "\n".join(f"- {a.name}: {a.job_description or a.role}" for a in agents)
    recent = "\n".join(
        f"[{m.agent_name or 'User'}] {m.content[:100]}" for m in recent_history[-3:]
    ) or "(no prior messages)"

    routing_prompt = (
        "Pick the single best agent to handle this user message. "
        "Reply with ONLY the agent name, nothing else.\n\n"
        f"Agents:\n{agent_list}\n\n"
        f"Recent conversation:\n{recent}\n\n"
        f"User message: {message}"
    )

    # Dispatch to root's worker as a one-off routing call
    stream_manager = RedisStreamManager(redis)
    dispatcher = WorkerDispatcher(stream_manager)
    worker = await dispatcher.find_available_worker(db)
    if not worker:
        return root

    chat_id = f"routing-{uuid.uuid4()}"
    await dispatcher.dispatch_chat(
        worker_id=worker.id,
        chat_id=chat_id,
        message=routing_prompt,
        system_prompt="You are a message router. Reply with only an agent name.",
        history=[],
    )

    result_key = f"chat:result:{chat_id}"
    waited = 0.0
    while waited < ROUTING_TIMEOUT:
        raw = await redis.get(result_key)
        if raw:
            await redis.delete(result_key)
            payload = json.loads(raw)
            if payload.get("success"):
                name = payload.get("output", "").strip()
                matched = next((a for a in agents if a.name.lower() == name.lower()), None)
                if matched:
                    logger.info("agent_routed_via_worker", agent=matched.name, message_preview=message[:50])
                    return matched
            break
        await asyncio.sleep(0.5)
        waited += 0.5

    logger.warning("worker_routing_fallback", reason="no_match_or_timeout")
    return root


# ── Prompt construction ─────────────────────────────────────────────


def _build_system_prompt(
    agent: models.Agent,
    project: models.Project,
    goal_context: str,
    all_agents: list[models.Agent],
    org_context: str = "",
) -> str:
    parts: list[str] = []
    if org_context:
        parts.append(org_context)
    if goal_context:
        parts.append(goal_context)
    parts.append(
        f"You are {agent.name}, a {agent.role} working on the project \"{project.name}\".\n"
        "Communicate professionally and respectfully — use polite language (존댓말 in Korean).\n"
        "Focus only on the project described below. Do not assume the product being built "
        "is the platform you are running on."
    )
    if agent.job_description:
        parts.append(f"Job description: {agent.job_description}")
    if agent.system_prompt:
        parts.append(agent.system_prompt)
    # Inject skill prompt fragments. Skills are reusable capability modules
    # (design, analyze, plan, memory_keeping) derived from
    # ``agent.capabilities`` via ``CAPABILITY_TO_SKILLS`` — that way a
    # custom agent created through the Hire Agent form picks up the right
    # skills automatically based on the capabilities the user checked,
    # without any role-based hardcoding.
    from backend.src.prompts.skills import render_skills_for_capabilities

    skill_block = render_skills_for_capabilities(agent.capabilities)
    if skill_block:
        parts.append(skill_block)
    parts.append(build_project_context(project))

    colleagues = [a for a in all_agents if a.id != agent.id and a.is_active]
    if colleagues:
        lines = ["Your team (you can @mention them to delegate or ask for input):"]
        for a in colleagues:
            desc = f"  - @{a.name} ({a.role})"
            if a.job_description:
                desc += f" — {a.job_description}"
            lines.append(desc)
        parts.append("\n".join(lines))

    parts.append(
        "You can create tasks by including markers in your response:\n"
        '[CREATE_TASK]{"title": "...", "description": "...", "priority": "medium", '
        '"task_type": "feature", "worker_prompt": "...", "qa_prompt": "..."}[/CREATE_TASK]\n\n'
        "You can set or update the project goal by including:\n"
        '[SET_GOAL]{"title": "...", "description": "...", "level": "project"}[/SET_GOAL]\n\n'
        "Only include these markers when the user explicitly asks you to create tasks or set goals.\n"
        "If another team member's expertise would be valuable, @mention them naturally in your response."
    )
    return "\n\n".join(parts)


def _strip_all_markers(text: str) -> str:
    text = strip_action_markers(text)
    text = SET_GOAL_RE.sub("", text).strip()
    return re.sub(r"^\[.*?\]\s*", "", text, count=1)


# ── Marker execution ────────────────────────────────────────────────


async def _ensure_active_phase(project_id: uuid.UUID, db: AsyncSession) -> models.Phase | None:
    """Return the active phase, creating a default one if none exists."""
    phase_repo = PhaseRepository(db)
    phases = await phase_repo.list_by_project(project_id)
    active = next((p for p in phases if p.status == models.PhaseStatus.active), None)
    if active:
        return active
    # Auto-create a default phase so task creation doesn't silently fail
    phase = models.Phase(
        project_id=project_id,
        name="Phase 1",
        description="Auto-created default phase",
        branch_name="phase/phase-1",
        order=1,
        status=models.PhaseStatus.active,
    )
    db.add(phase)
    await db.flush()
    logger.info("auto_created_phase", project_id=str(project_id), phase_id=str(phase.id))
    return phase


async def _execute_create_task_markers(
    text: str, project_id: uuid.UUID, agent_id: uuid.UUID, db: AsyncSession, redis: Any,
) -> list[dict[str, Any]]:
    active_phase = await _ensure_active_phase(project_id, db)
    if not active_phase:
        return []

    actions: list[dict[str, Any]] = []
    created_tasks: list[models.Task] = []

    for match in CREATE_TASK_RE.finditer(text):
        try:
            task_data = json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            continue
        try:
            priority = models.TaskPriority(task_data.get("priority", "medium"))
        except ValueError:
            priority = models.TaskPriority.medium
        try:
            task_type = models.TaskType(task_data.get("task_type", "feature"))
        except ValueError:
            task_type = models.TaskType.feature

        new_task = models.Task(
            project_id=project_id,
            phase_id=active_phase.id,
            title=task_data.get("title", "Untitled Task"),
            description=task_data.get("description"),
            priority=priority,
            task_type=task_type,
            source=models.TaskSource.llm,
            status=models.TaskStatus.pending,
            agent_id=agent_id,
            worker_prompt={"prompt": task_data.get("worker_prompt", "")},
            qa_prompt={"prompt": task_data.get("qa_prompt", "")},
            branch_name=active_phase.branch_name,
        )
        db.add(new_task)
        await db.flush()
        created_tasks.append(new_task)
        actions.append({"type": "task_created", "task_id": str(new_task.id), "title": new_task.title})

    # Auto-dispatch newly created tasks to an available worker so they execute
    # immediately instead of waiting for a manually started orchestrator.
    if created_tasks and redis is not None:
        stream_manager = RedisStreamManager(redis)
        dispatcher = WorkerDispatcher(stream_manager)
        worker = await dispatcher.find_available_worker(db)
        if worker:
            for task in created_tasks:
                prompt = (task.worker_prompt or {}).get("prompt") or task.title
                try:
                    await dispatcher.dispatch_task(
                        worker_id=worker.id,
                        task_id=task.id,
                        task_title=task.title,
                        project_id=str(project_id),
                        prompt=prompt,
                    )
                    task.status = models.TaskStatus.running
                    await db.flush()
                except Exception as e:
                    logger.warning("auto_dispatch_failed", task_id=str(task.id), error=str(e))

    return actions


async def _execute_goal_markers(
    text: str, project_id: uuid.UUID, db: AsyncSession, tenant_id: uuid.UUID,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for match in SET_GOAL_RE.finditer(text):
        try:
            goal_data = json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            continue
        title = goal_data.get("title", "").strip()
        if not title:
            continue
        level = goal_data.get("level", "project")
        description = goal_data.get("description")

        result = await db.execute(
            select(models.Goal).where(
                models.Goal.project_id == project_id,
                models.Goal.level == level,
            ).limit(1)
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.title = title
            if description:
                existing.description = description
            actions.append({"type": "goal_updated", "goal_id": str(existing.id), "title": title})
        else:
            new_goal = models.Goal(
                tenant_id=tenant_id, level=level, title=title,
                description=description, project_id=project_id,
            )
            db.add(new_goal)
            await db.flush()
            actions.append({"type": "goal_created", "goal_id": str(new_goal.id), "title": title})
    return actions


# ── Persistence + event publishing ──────────────────────────────────


def _message_to_event(msg: models.ConversationMessage) -> dict[str, Any]:
    return {
        "id": str(msg.id),
        "role": msg.role,
        "content": msg.content,
        "agent_id": str(msg.agent_id) if msg.agent_id else None,
        "agent_name": msg.agent_name,
        "actions": msg.actions or [],
        "created_at": msg.created_at.isoformat(),
    }


async def _publish_event(redis: Any, project_id: uuid.UUID, event: str, data: dict[str, Any]) -> None:
    if redis is None:
        return
    stream_manager = RedisStreamManager(redis)
    stream = RedisStreamManager.chat_events_stream(str(project_id))
    await stream_manager.publish(stream, {"event": event, "data": data})
    try:
        await redis.xtrim(stream, maxlen=500, approximate=True)
    except Exception:
        pass


async def _store_and_publish(
    db: AsyncSession, redis: Any, project_id: uuid.UUID, *,
    role: str, content: str,
    agent: models.Agent | None = None,
    actions: list[dict[str, Any]] | None = None,
    source: str = "web",
) -> models.ConversationMessage:
    repo = ConversationRepository(db)
    msg = await repo.append(
        project_id, role=role, content=content,
        agent_id=agent.id if agent else None,
        agent_name=agent.name if agent else None,
        actions=actions, source=source,
    )
    await db.commit()
    await _publish_event(redis, project_id, "message_created", _message_to_event(msg))
    return msg


async def _publish_agent_status(redis: Any, project_id: uuid.UUID, agent: models.Agent, status: str) -> None:
    """Publish agent_status SSE event (busy/online).

    Pushed to BOTH the chat events stream (so the chat sidebar can react)
    and the plan events stream (so the Plan view's AgentStatusBar
    refreshes its dot without polling).
    """
    payload = {
        "agent_id": str(agent.id),
        "agent_name": agent.name,
        "status": status,
    }
    await _publish_event(redis, project_id, "agent_status", payload)
    if redis is None:
        return
    stream_manager = RedisStreamManager(redis)
    plan_stream = RedisStreamManager.project_events_stream(str(project_id))
    try:
        await stream_manager.publish(
            plan_stream,
            {"event": "agent_status_changed", "data": payload},
        )
        await redis.xtrim(plan_stream, maxlen=500, approximate=True)
    except Exception:  # noqa: BLE001
        pass


# ── LLM / worker dispatch ───────────────────────────────────────────


async def _resolve_llm_config(agent: models.Agent, db: AsyncSession) -> LLMConfig:
    default_model = settings.default_llm_model
    if agent.executor_config_id:
        result = await db.execute(
            select(models.ExecutorConfig).where(models.ExecutorConfig.id == agent.executor_config_id)
        )
        exec_cfg = result.scalar_one_or_none()
        if exec_cfg and exec_cfg.config and exec_cfg.config.get("api_key"):
            cfg = exec_cfg.config
            return LLMConfig(
                api_key=cfg["api_key"],
                model=cfg.get("model", default_model),
                base_url=cfg.get("base_url"),
            )

    raw = await get_raw_llm_config(db)
    api_key = raw.get("llm_api_key")
    if not api_key:
        raise HTTPException(status_code=400, detail="No LLM API key configured.")
    return LLMConfig(
        api_key=api_key,
        model=raw.get("llm_model", default_model),
        base_url=raw.get("llm_base_url"),
    )


async def _build_org_context(tenant_id: uuid.UUID, db: AsyncSession) -> str:
    """Load tenant-level mission goals and format them for the prompt.

    Org-level goals are the stable cross-session context every agent
    must keep in mind. They live above any project — typically the
    company mission, top-level OKRs, or principles the founder set.
    Loading them on every turn replaces the noisier "dump the latest
    N memories" approach we tried first.
    """
    result = await db.execute(
        select(models.Goal).where(
            models.Goal.tenant_id == tenant_id,
            models.Goal.level == "mission",
        ).order_by(models.Goal.created_at.asc())
    )
    org_goals = list(result.scalars().all())
    if not org_goals:
        return ""
    lines = ["[Organization mission — keep this in mind on every turn]"]
    for goal in org_goals:
        lines.append(f"- {goal.title}")
        if goal.description:
            lines.append(f"  {goal.description}")
    return "\n".join(lines)


async def _build_chat_context(
    agent: models.Agent, project: models.Project, project_id: uuid.UUID,
    history: list[models.ConversationMessage], user_message: str,
    db: AsyncSession, all_agents: list[models.Agent],
    *, tenant_id: uuid.UUID,
) -> tuple[str, list[dict[str, str]]]:
    goal_svc = GoalAlignmentService(db)
    goal_result = await db.execute(
        select(models.Goal).where(
            models.Goal.project_id == project_id,
            models.Goal.level == "project",
        ).limit(1)
    )
    project_goal = goal_result.scalar_one_or_none()
    goal_context = await goal_svc.build_goal_context(project_goal.id) if project_goal else ""

    org_context = await _build_org_context(tenant_id, db)
    system_prompt = _build_system_prompt(
        agent, project, goal_context, all_agents=all_agents, org_context=org_context
    )
    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    for h in history:
        content = h.content
        if h.role == "assistant" and h.agent_name:
            content = f"[{h.agent_name}] {content}"
        messages.append({"role": h.role, "content": content})
    messages.append({"role": "user", "content": user_message})
    return system_prompt, messages


async def _process_response_text(
    response_text: str, project: models.Project, project_id: uuid.UUID,
    agent: models.Agent, db: AsyncSession, redis: Any,
    *, tenant_id: uuid.UUID,
) -> models.ConversationMessage:
    task_actions = await _execute_create_task_markers(response_text, project_id, agent.id, db, redis)
    goal_actions = await _execute_goal_markers(response_text, project_id, db, tenant_id)
    cleaned = _strip_all_markers(response_text)
    return await _store_and_publish(
        db, redis, project_id, role="assistant", content=cleaned,
        agent=agent, actions=task_actions + goal_actions,
    )


async def _call_via_llm(
    agent: models.Agent, project: models.Project, project_id: uuid.UUID,
    history: list[models.ConversationMessage], user_message: str,
    db: AsyncSession, redis: Any, all_agents: list[models.Agent],
    *, tenant_id: uuid.UUID,
) -> models.ConversationMessage:
    llm_config = await _resolve_llm_config(agent, db)
    _, messages = await _build_chat_context(
        agent, project, project_id, history, user_message, db, all_agents, tenant_id=tenant_id
    )
    client = LLMClient(llm_config)
    response_text = await client.chat(messages)
    return await _process_response_text(
        response_text, project, project_id, agent, db, redis, tenant_id=tenant_id
    )


async def _call_via_worker(
    agent: models.Agent, project: models.Project, project_id: uuid.UUID,
    history: list[models.ConversationMessage], user_message: str,
    db: AsyncSession, redis: Any, all_agents: list[models.Agent],
    *, tenant_id: uuid.UUID,
) -> models.ConversationMessage:
    if redis is None:
        raise HTTPException(status_code=500, detail="Redis not available for worker dispatch")

    worker_id: uuid.UUID | None = None
    if agent.executor_config_id:
        result = await db.execute(
            select(models.ExecutorConfig).where(models.ExecutorConfig.id == agent.executor_config_id)
        )
        exec_cfg = result.scalar_one_or_none()
        if exec_cfg and exec_cfg.config.get("worker_id"):
            worker_id = uuid.UUID(exec_cfg.config["worker_id"])

    stream_manager = RedisStreamManager(redis)
    dispatcher = WorkerDispatcher(stream_manager)

    if worker_id:
        result = await db.execute(
            select(models.Worker).where(models.Worker.id == worker_id, models.Worker.is_active.is_(True))
        )
        worker = result.scalar_one_or_none()
        if not worker or worker.status != "online":
            raise HTTPException(status_code=503, detail=f"Worker offline: {worker.name if worker else 'unknown'}")
    else:
        worker = await dispatcher.find_available_worker(db)
        if not worker:
            raise HTTPException(status_code=503, detail="No online worker available.")

    system_prompt, _ = await _build_chat_context(
        agent, project, project_id, history, user_message, db, all_agents, tenant_id=tenant_id,
    )
    flat_history: list[dict[str, str]] = []
    for h in history:
        content = h.content
        if h.role == "assistant" and h.agent_name:
            content = f"[{h.agent_name}] {content}"
        flat_history.append({"role": h.role, "content": content})

    chat_id = str(uuid.uuid4())
    await dispatcher.dispatch_chat(
        worker_id=worker.id, chat_id=chat_id, message=user_message,
        system_prompt=system_prompt, history=flat_history,
    )

    result_key = f"chat:result:{chat_id}"
    waited = 0.0
    while waited < WORKER_RESULT_TIMEOUT:
        raw = await redis.get(result_key)
        if raw:
            await redis.delete(result_key)
            payload = json.loads(raw)
            if not payload.get("success", False):
                raise HTTPException(status_code=502, detail=f"Worker error: {payload.get('error_message', 'failed')}")
            return await _process_response_text(
                payload.get("output", ""), project, project_id, agent, db, redis,
                tenant_id=tenant_id,
            )
        await asyncio.sleep(0.5)
        waited += 0.5

    raise HTTPException(status_code=504, detail=f"Worker timed out ({int(WORKER_RESULT_TIMEOUT)}s).")


async def _call_agent(
    agent: models.Agent, project: models.Project, project_id: uuid.UUID,
    history: list[models.ConversationMessage], user_message: str,
    db: AsyncSession, redis: Any, all_agents: list[models.Agent],
    *, tenant_id: uuid.UUID,
) -> models.ConversationMessage:
    """Route to executor; fall back to worker if LLM is unconfigured."""
    if agent.executor_type == "worker":
        return await _call_via_worker(
            agent, project, project_id, history, user_message, db, redis, all_agents,
            tenant_id=tenant_id,
        )
    try:
        return await _call_via_llm(
            agent, project, project_id, history, user_message, db, redis, all_agents,
            tenant_id=tenant_id,
        )
    except HTTPException as e:
        if e.status_code == 400 and "No LLM API key" in str(e.detail):
            stream_manager = RedisStreamManager(redis) if redis else None
            dispatcher = WorkerDispatcher(stream_manager) if stream_manager else None
            worker = await dispatcher.find_available_worker(db) if dispatcher else None
            if worker:
                return await _call_via_worker(
                    agent, project, project_id, history, user_message, db, redis, all_agents,
                    tenant_id=tenant_id,
                )
        raise


# ── Background agent processing ─────────────────────────────────────


async def _process_agent_in_background(
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    user_message: str,
    redis: Any,
    tenant_id: uuid.UUID,
) -> None:
    """Run a single agent in the background. Fresh DB session, publishes via SSE.

    Handles delegation chains: if the agent's response @mentions others,
    they are dispatched as further background tasks. There is no depth
    limit — agents collaborate freely. Loop prevention is a prompt-level
    concern, not an infrastructure one.
    """
    from backend.src.core.agent_activity import clear_agent_busy, mark_agent_busy

    # Mark busy upfront so the Plan view + Agents tab pick up the
    # transient state even if the agent lookup below stalls. Cleared in
    # the finally block so a crash never leaves the indicator stuck.
    await mark_agent_busy(redis, tenant_id, agent_id)
    agent: models.Agent | None = None
    async with async_session() as db:
        try:
            # Reload project + agent in this session
            project_result = await db.execute(
                select(models.Project)
                .where(models.Project.id == project_id)
                .options(selectinload(models.Project.phases).selectinload(models.Phase.tasks))
            )
            project = project_result.scalar_one_or_none()
            if not project:
                return

            agents_result = await db.execute(
                select(models.Agent).where(
                    models.Agent.tenant_id == tenant_id,
                    models.Agent.is_active.is_(True),
                )
            )
            all_agents = list(agents_result.scalars().all())
            agent = next((a for a in all_agents if a.id == agent_id), None)
            if not agent:
                return

            await _publish_agent_status(redis, project_id, agent, "busy")

            history = await ConversationRepository(db).list_by_project(project_id, limit=MAX_HISTORY)
            msg = await _call_agent(
                agent, project, project_id, history, user_message, db, redis, all_agents,
                tenant_id=tenant_id,
            )

            await _publish_agent_status(redis, project_id, agent, "online")

            # Delegation: dispatch further agents @mentioned in this response.
            # No depth limit, no called_ids dedup — the same agent CAN be
            # called again if a different colleague mentions them with a new
            # request. Loop prevention is a prompt responsibility.
            delegated = _parse_mentions(msg.content, all_agents)
            for delegate in delegated:
                asyncio.create_task(_process_agent_in_background(
                    project_id, delegate.id, msg.content, redis, tenant_id,
                ))

        except Exception as e:
            logger.error("background_agent_failed", agent_id=str(agent_id), error=str(e))
            # Publish error as an assistant message attributed to the
            # failed agent so the frontend's typing-indicator filter
            # picks it up and clears the spinner. If the agent failed to
            # load, look up name + id one more time so the indicator
            # still clears.
            try:
                error_agent = agent
                if error_agent is None:
                    lookup = await db.execute(
                        select(models.Agent).where(models.Agent.id == agent_id)
                    )
                    error_agent = lookup.scalar_one_or_none()
                await _store_and_publish(
                    db, redis, project_id,
                    role="assistant", content=f"[Error] {e}",
                    agent=error_agent,
                )
                if error_agent is not None:
                    await _publish_agent_status(redis, project_id, error_agent, "online")
            except Exception:  # noqa: BLE001
                pass
        finally:
            await clear_agent_busy(redis, tenant_id, agent_id)


# ── Endpoints ───────────────────────────────────────────────────────


def _msg_to_out(msg: models.ConversationMessage) -> ChatMessageOut:
    return ChatMessageOut(
        id=msg.id, role=msg.role, content=msg.content,
        agent_id=msg.agent_id, agent_name=msg.agent_name,
        actions=msg.actions or [], created_at=msg.created_at,
    )


@router.post("", response_model=ChatDispatchResponse)
async def chat_with_agent(
    project_id: uuid.UUID,
    body: ChatRequest,
    request: Request,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_update)),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> ChatDispatchResponse:
    """Fire-and-forget: store user message, dispatch agents, return immediately."""
    redis = getattr(request.app.state, "redis", None)

    # Validate project exists
    project_result = await db.execute(
        select(models.Project).where(models.Project.id == project_id)
    )
    if not project_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")

    # Load agents
    agents_result = await db.execute(
        select(models.Agent).where(
            models.Agent.tenant_id == tenant_id,
            models.Agent.is_active.is_(True),
        )
    )
    all_agents = list(agents_result.scalars().all())
    if not all_agents:
        raise HTTPException(status_code=400, detail="No agents configured.")

    # Resolve targets: explicit @mentions → worker routing → org root fallback
    mentioned = _parse_mentions(body.message, all_agents)
    if not mentioned:
        history = await ConversationRepository(db).list_by_project(project_id, limit=MAX_HISTORY)
        routed = await _route_via_worker(body.message, all_agents, history, db, redis)
        mentioned = [routed] if routed else [_find_org_root(all_agents)]
    mentioned = [a for a in mentioned if a is not None]

    # Persist user message + publish to SSE
    await _store_and_publish(db, redis, project_id, role="user", content=body.message)

    # Dispatch all agents in parallel as background tasks.
    # No called_ids tracking — delegation is unlimited.
    for agent in mentioned:
        asyncio.create_task(_process_agent_in_background(
            project_id, agent.id, body.message, redis, tenant_id,
        ))

    return ChatDispatchResponse(dispatched_agents=[a.name for a in mentioned])


@router.get("", response_model=ChatHistoryResponse)
async def get_chat_history(
    project_id: uuid.UUID,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_read)),
    db: AsyncSession = Depends(get_db),
) -> ChatHistoryResponse:
    history = await ConversationRepository(db).list_by_project(project_id, limit=MAX_HISTORY)
    return ChatHistoryResponse(messages=[_msg_to_out(m) for m in history])


async def _chat_event_generator(
    project_id: uuid.UUID, redis: Any,
) -> AsyncGenerator[dict, None]:
    stream_manager = RedisStreamManager(redis)
    stream = RedisStreamManager.chat_events_stream(str(project_id))
    last_id = "$"
    while True:
        try:
            entries = await stream_manager.tail(stream, last_id=last_id, block=15000)
            for entry in entries:
                last_id = entry.pop("_message_id")
                yield {
                    "event": entry.get("event", "message"),
                    "data": json.dumps(entry.get("data", {})),
                }
        except asyncio.CancelledError:
            break
        except Exception:
            logger.warning("chat_sse_error", project_id=str(project_id), exc_info=True)
            await asyncio.sleep(1)


@router.get("/events")
async def chat_events(
    project_id: uuid.UUID,
    request: Request,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_read)),
) -> EventSourceResponse:
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis not available")
    return EventSourceResponse(_chat_event_generator(project_id, redis))


@router.delete("")
async def clear_chat_history(
    project_id: uuid.UUID,
    request: Request,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_update)),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    repo = ConversationRepository(db)
    await repo.clear(project_id)
    await db.commit()
    redis = getattr(request.app.state, "redis", None)
    await _publish_event(redis, project_id, "history_cleared", {})
    return {"detail": "Chat history cleared"}
