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
STATUS_RE = re.compile(r"^\[STATUS\]\s*(.+?)$", re.MULTILINE)
DECISION_RE = re.compile(r"\[DECISION\](.*?)\[/DECISION\]", re.DOTALL)
MAX_HISTORY = 100
# Per-agent timeout for waiting on a worker chat result. This is NOT a
# chain-wide limit — each agent's _call_via_worker polls independently.
# Design / coding tasks can take 10-30 minutes; set generously.
WORKER_RESULT_TIMEOUT = 1800.0  # 30 minutes
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


_MENTION_RE = re.compile(r"@\S+")


def _summarize_activity(message: str, agent_name: str = "", max_len: int = 50) -> str:
    """Build a short "doing what" activity label from a chat message.

    Examples:
      "@CMO 시장 조사해서 CEO에게 보고해"
        → "시장 조사해서 CEO에게 보고해"

      (CEO's long response containing "@CTO 님께 — 기술 스택 및 아키텍처 방향...")
        → "기술 스택 및 아키텍처 방향 검토 중"

    Strategy:
    1. For delegation chains (message > 200 chars): find the line that
       mentions @agent_name and extract the request from that line.
    2. For direct user messages: strip @mentions.
    3. Append "중" (Korean "in progress") suffix if the text looks like
       a verb phrase, making it read as "시장 조사 중" not "시장 조사".
    """
    raw = ""

    # For delegation: find the line mentioning this agent.
    if agent_name and len(message) > 200:
        for line in message.split("\n"):
            if f"@{agent_name}" in line:
                raw = line
                break

    if not raw:
        raw = message

    # Strip @mentions, markdown cruft, collapse whitespace.
    stripped = _MENTION_RE.sub("", raw).strip()
    stripped = stripped.lstrip("-—·•#>").strip()
    stripped = " ".join(stripped.split())

    if not stripped:
        return ""

    # Truncate.
    if len(stripped) > max_len:
        stripped = stripped[:max_len].rstrip() + "..."

    # Append "중" if the text doesn't already end with it and looks
    # like a Korean verb phrase (ends in 하다/해/요/줘/etc patterns).
    # Simple heuristic: if it contains Korean characters and doesn't
    # already end with "중" or "...", append " 중".
    has_korean = any("\uac00" <= ch <= "\ud7a3" for ch in stripped)
    if has_korean and not stripped.endswith("중") and not stripped.endswith("..."):
        stripped += " 중"

    return stripped


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


async def _build_system_prompt(
    agent: models.Agent,
    project: models.Project,
    goal_context: str,
    all_agents: list[models.Agent],
    org_context: str = "",
    active_decisions: list[str] | None = None,
) -> str:
    """Build the system prompt via the harness (workspace-based modules).

    Falls back to inline defaults for projects without a workspace or
    without a ``.bsnexus/`` directory.
    """
    from backend.src.core.harness import assemble_system_prompt, seed_harness

    workspace_dir = project.workspace_dir
    if workspace_dir:
        seed_harness(workspace_dir)

    return await assemble_system_prompt(
        agent, project, workspace_dir,
        goal_context=goal_context,
        org_context=org_context,
        all_agents=all_agents,
        active_decisions=active_decisions,
    )


def _extract_status(text: str) -> str:
    """Pull the [STATUS] line from a response. Returns empty if absent."""
    m = STATUS_RE.search(text)
    return m.group(1).strip()[:80] if m else ""


def _extract_decisions(text: str) -> list[str]:
    """Pull all [DECISION] blocks from a response."""
    return [m.strip() for m in DECISION_RE.findall(text) if m.strip()]


def _strip_all_markers(text: str) -> str:
    text = strip_action_markers(text)
    text = SET_GOAL_RE.sub("", text).strip()
    text = STATUS_RE.sub("", text).strip()
    text = DECISION_RE.sub("", text).strip()
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


def _load_decisions_from_workspace(workspace_dir: str | None) -> list[str]:
    """Read active decisions from .bsnexus/context/decisions.md."""
    if not workspace_dir:
        return []
    from pathlib import Path

    from backend.src.core.harness import HARNESS_DIR

    f = Path(workspace_dir) / HARNESS_DIR / "context" / "decisions.md"
    if not f.is_file():
        return []
    decisions: list[str] = []
    for line in f.read_text().splitlines():
        stripped = line.lstrip("0123456789. ").strip()
        if stripped and not line.startswith("#") and not line.startswith("These"):
            decisions.append(stripped)
    return decisions


# Capabilities that grant the right to create [DECISION] markers.
# Only strategic / decision-making roles should confirm directions.
_DECISION_CAPABILITIES = {"plan"}


async def _execute_decision_markers(
    text: str, project: models.Project, agent: models.Agent,
) -> list[dict[str, Any]]:
    """Parse [DECISION] markers and append to .bsnexus/context/decisions.md.

    Only agents with a decision-capable capability (``plan``) can create
    decisions. Non-qualifying agents that emit [DECISION] markers are
    silently ignored — this keeps the infrastructure gate simple while
    the prompt-level rule already tells most agents not to do it.
    """
    from pathlib import Path

    from backend.src.core.harness import HARNESS_DIR

    actions: list[dict[str, Any]] = []
    decisions = _extract_decisions(text)
    if not decisions:
        return actions

    # Capability gate
    agent_caps = {(c or "").strip().lower() for c in (agent.capabilities or [])}
    if not agent_caps & _DECISION_CAPABILITIES:
        logger.info(
            "decision_ignored_no_capability",
            agent=agent.name,
            capabilities=list(agent_caps),
            count=len(decisions),
        )
        return actions

    workspace_dir = project.workspace_dir
    if not workspace_dir:
        logger.warning("decision_no_workspace", agent=agent.name)
        return actions

    decisions_file = Path(workspace_dir) / HARNESS_DIR / "context" / "decisions.md"
    decisions_file.parent.mkdir(parents=True, exist_ok=True)

    # Read existing decisions to dedup
    existing: set[str] = set()
    if decisions_file.is_file():
        for line in decisions_file.read_text().splitlines():
            stripped = line.lstrip("0123456789. ").strip()
            if stripped:
                existing.add(stripped.lower())

    new_decisions: list[str] = []
    for title in decisions:
        if not title or title.lower() in existing:
            continue
        new_decisions.append(title)
        actions.append({
            "type": "decision_created",
            "title": title,
        })
        logger.info(
            "decision_created",
            project_id=str(project.id),
            agent=agent.name,
            title=title,
        )

    if new_decisions:
        # Rebuild the file with header + numbered list
        all_decisions = []
        if decisions_file.is_file():
            for line in decisions_file.read_text().splitlines():
                stripped = line.lstrip("0123456789. ").strip()
                if stripped and not line.startswith("#"):
                    all_decisions.append(stripped)
        all_decisions.extend(new_decisions)

        lines = [
            "# Active Decisions\n",
            "These are confirmed project directions. Do NOT contradict them.\n",
        ]
        for i, d in enumerate(all_decisions, 1):
            lines.append(f"{i}. {d}")
        decisions_file.write_text("\n".join(lines))

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

    # Load active decisions from .bsnexus/context/decisions.md (file-based,
    # single source of truth — no DB table).
    active_decisions = _load_decisions_from_workspace(project.workspace_dir)

    system_prompt = await _build_system_prompt(
        agent, project, goal_context, all_agents=all_agents,
        org_context=org_context, active_decisions=active_decisions,
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
    decision_actions = await _execute_decision_markers(
        response_text, project, agent,
    )
    cleaned = _strip_all_markers(response_text)
    return await _store_and_publish(
        db, redis, project_id, role="assistant", content=cleaned,
        agent=agent, actions=task_actions + goal_actions + decision_actions,
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

    # Poll for the result WITHOUT holding the DB session — the polling
    # loop only touches Redis. Once the result arrives, open a fresh
    # session for _process_response_text. This prevents long-running
    # worker turns (up to 30 min) from exhausting the connection pool.
    result_key = f"chat:result:{chat_id}"
    waited = 0.0
    while waited < WORKER_RESULT_TIMEOUT:
        raw = await redis.get(result_key)
        if raw:
            await redis.delete(result_key)
            payload = json.loads(raw)
            if not payload.get("success", False):
                raise HTTPException(status_code=502, detail=f"Worker error: {payload.get('error_message', 'failed')}")
            # Open a fresh, short-lived session for response processing
            # (markers, persist, publish). The caller's session may be
            # closed by now if we're in a background task.
            async with async_session() as fresh_db:
                return await _process_response_text(
                    payload.get("output", ""), project, project_id, agent, fresh_db, redis,
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

    # Initial busy mark with a placeholder — updated with a better
    # summary once we load the agent name from the DB.
    await mark_agent_busy(redis, tenant_id, agent_id, activity="")
    agent: models.Agent | None = None

    # Phase 1: short-lived DB session for setup (load project, agent,
    # history, build prompt). Closed before the long worker wait so we
    # don't hold a connection pool slot for 30 minutes.
    project = None
    all_agents: list[models.Agent] = []
    history: list[models.ConversationMessage] = []
    async with async_session() as setup_db:
        project_result = await setup_db.execute(
            select(models.Project)
            .where(models.Project.id == project_id)
            .options(selectinload(models.Project.phases).selectinload(models.Phase.tasks))
        )
        project = project_result.scalar_one_or_none()
        if not project:
            await clear_agent_busy(redis, tenant_id, agent_id)
            return

        agents_result = await setup_db.execute(
            select(models.Agent).where(
                models.Agent.tenant_id == tenant_id,
                models.Agent.is_active.is_(True),
            )
        )
        all_agents = list(agents_result.scalars().all())
        agent = next((a for a in all_agents if a.id == agent_id), None)
        if not agent:
            await clear_agent_busy(redis, tenant_id, agent_id)
            return

        activity = _summarize_activity(user_message, agent_name=agent.name)
        await mark_agent_busy(redis, tenant_id, agent_id, activity=activity)
        await _publish_agent_status(redis, project_id, agent, "busy")

        history = await ConversationRepository(setup_db).list_by_project(project_id, limit=MAX_HISTORY)
    # setup_db is now CLOSED — connection returned to pool.

    # Phase 2: call agent (may block for minutes on worker polling).
    # _call_via_worker opens its own fresh session for response processing.
    # _call_via_llm is fast (seconds) but also uses a fresh session via
    # the passed db — we open a short-lived one here.
    try:
        async with async_session() as call_db:
            msg = await _call_agent(
                agent, project, project_id, history, user_message, call_db, redis, all_agents,
                tenant_id=tenant_id,
            )

            await _publish_agent_status(redis, project_id, agent, "online")

            # Delegation: dispatch further agents @mentioned in this response.
            delegated = _parse_mentions(msg.content, all_agents)
            for delegate in delegated:
                asyncio.create_task(_process_agent_in_background(
                    project_id, delegate.id, msg.content, redis, tenant_id,
                ))

    except Exception as e:
        logger.error("background_agent_failed", agent_id=str(agent_id), error=str(e))
        try:
            error_agent = agent
            if error_agent is None:
                async with async_session() as err_db:
                    lookup = await err_db.execute(
                        select(models.Agent).where(models.Agent.id == agent_id)
                    )
                    error_agent = lookup.scalar_one_or_none()
            async with async_session() as err_db:
                await _store_and_publish(
                    err_db, redis, project_id,
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
