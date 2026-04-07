"""Unified Project Chat API — Paperclip-style conversation with @mention routing.

Single conversation thread per project. Users @mention agents to direct messages.
Responses may contain [CREATE_TASK] and [SET_GOAL] markers for auto-creation.
"""

from __future__ import annotations

import collections
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from bsvibe_auth import BSVibeUser
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.src import models
from backend.src.config import settings
from backend.src.core.architect_service import CREATE_TASK_RE, build_project_context, strip_action_markers
from backend.src.core.auth import Permission, require_permission
from backend.src.core.goal_alignment import GoalAlignmentService
from backend.src.core.llm_client import LLMClient, LLMConfig
from backend.src.core.tenant_context import DEFAULT_TENANT_ID
from backend.src.api.settings import get_raw_llm_config
from backend.src.repositories.phase_repository import PhaseRepository
from backend.src.storage.database import get_db

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/projects/{project_id}/chat", tags=["agent-chat"])

_MAX_CONVERSATIONS = 200
_MAX_MESSAGES_PER_CONVERSATION = 100

SET_GOAL_RE = re.compile(r"\[SET_GOAL\](.*?)\[/SET_GOAL\]", re.DOTALL)

# ── Request / Response schemas ──────────────────────────────────────


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=10000)


class ChatMessageOut(BaseModel):
    id: str
    role: str
    content: str
    agent_id: str | None = None
    agent_name: str | None = None
    created_at: str
    actions: list[dict[str, Any]] = Field(default_factory=list)


class ChatResponse(BaseModel):
    messages: list[ChatMessageOut]


class ChatHistoryResponse(BaseModel):
    messages: list[ChatMessageOut]


# ── In-memory chat history (per project, LRU-bounded) ───────────────

_chat_histories: collections.OrderedDict[str, list[dict[str, Any]]] = collections.OrderedDict()


def _history_key(project_id: uuid.UUID) -> str:
    return str(project_id)


def _get_history(project_id: uuid.UUID) -> list[dict[str, Any]]:
    key = _history_key(project_id)
    if key in _chat_histories:
        _chat_histories.move_to_end(key)
        return _chat_histories[key]
    history: list[dict[str, Any]] = []
    _chat_histories[key] = history
    while len(_chat_histories) > _MAX_CONVERSATIONS:
        _chat_histories.popitem(last=False)
    return history


def _add_message(
    project_id: uuid.UUID,
    role: str,
    content: str,
    *,
    agent_id: uuid.UUID | None = None,
    agent_name: str | None = None,
    actions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    history = _get_history(project_id)
    msg = {
        "id": str(uuid.uuid4()),
        "role": role,
        "content": content,
        "agent_id": str(agent_id) if agent_id else None,
        "agent_name": agent_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "actions": actions or [],
    }
    history.append(msg)
    if len(history) > _MAX_MESSAGES_PER_CONVERSATION:
        del history[: len(history) - _MAX_MESSAGES_PER_CONVERSATION]
    return msg


# ── @Mention parsing ────────────────────────────────────────────────


def _parse_mentions(message: str, agents: list[models.Agent]) -> list[models.Agent]:
    """Parse @AgentName mentions from message text. Returns matched agents in order."""
    mentioned: list[models.Agent] = []
    seen: set[uuid.UUID] = set()
    # Sort agents by name length descending to match longer names first
    sorted_agents = sorted(agents, key=lambda a: len(a.name), reverse=True)
    lower_msg = message.lower()
    for agent in sorted_agents:
        if agent.id in seen:
            continue
        pattern = f"@{agent.name.lower()}"
        if pattern in lower_msg:
            mentioned.append(agent)
            seen.add(agent.id)
    # Preserve order of appearance in message
    if len(mentioned) > 1:
        mentioned.sort(key=lambda a: lower_msg.index(f"@{a.name.lower()}"))
    return mentioned


def _pick_default_agent(agents: list[models.Agent]) -> models.Agent | None:
    """Pick a default agent when no @mention is found."""
    if not agents:
        return None
    # Prefer PM/CTO roles
    for agent in agents:
        if any(r in agent.role.lower() for r in ("pm", "cto", "manager", "lead")):
            return agent
    return agents[0]


# ── Helpers ─────────────────────────────────────────────────────────


def _build_system_prompt(agent: models.Agent, project: models.Project, goal_context: str) -> str:
    """Build system prompt for agent chat from agent config + project context."""
    parts: list[str] = []

    if goal_context:
        parts.append(goal_context)

    parts.append(f"You are {agent.name}, a {agent.role} working on the project \"{project.name}\".")

    if agent.job_description:
        parts.append(f"Job description: {agent.job_description}")

    if agent.system_prompt:
        parts.append(agent.system_prompt)

    parts.append(build_project_context(project))

    parts.append(
        "You can create tasks by including markers in your response:\n"
        '[CREATE_TASK]{"title": "...", "description": "...", "priority": "medium", '
        '"task_type": "feature", "worker_prompt": "...", "qa_prompt": "..."}[/CREATE_TASK]\n\n'
        "You can set or update the project goal by including:\n"
        '[SET_GOAL]{"title": "...", "description": "...", "level": "project"}[/SET_GOAL]\n\n'
        "Only include these markers when the user explicitly asks you to create tasks or set goals."
    )

    return "\n\n".join(parts)


def _strip_all_markers(text: str) -> str:
    """Remove both action and goal markers from user-visible text."""
    text = strip_action_markers(text)
    return SET_GOAL_RE.sub("", text).strip()


async def _resolve_llm_config(agent: models.Agent, db: AsyncSession) -> LLMConfig:
    """Resolve LLM config: agent executor config -> global settings -> default model."""
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
        raise HTTPException(status_code=400, detail="No LLM API key configured. Set one in Settings or on the agent's executor.")
    return LLMConfig(
        api_key=api_key,
        model=raw.get("llm_model", default_model),
        base_url=raw.get("llm_base_url"),
    )


async def _execute_create_task_markers(
    text: str, project_id: uuid.UUID, agent_id: uuid.UUID, db: AsyncSession
) -> list[dict[str, Any]]:
    """Parse [CREATE_TASK] markers and create tasks in the active phase."""
    actions: list[dict[str, Any]] = []

    phase_repo = PhaseRepository(db)
    phases = await phase_repo.list_by_project(project_id)
    active_phase = next((p for p in phases if p.status == models.PhaseStatus.active), None)

    for match in CREATE_TASK_RE.finditer(text):
        if not active_phase:
            break
        try:
            task_data = json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            logger.warning("agent_chat_invalid_task_marker", raw=match.group(1)[:200])
            continue

        priority_str = task_data.get("priority", "medium")
        try:
            priority = models.TaskPriority(priority_str)
        except ValueError:
            priority = models.TaskPriority.medium

        task_type_str = task_data.get("task_type", "feature")
        try:
            task_type = models.TaskType(task_type_str)
        except ValueError:
            task_type = models.TaskType.feature

        new_task = models.Task(
            project_id=project_id,
            phase_id=active_phase.id,
            title=task_data.get("title", "Untitled Task"),
            description=task_data.get("description"),
            priority=priority,
            task_type=task_type,
            source=models.TaskSource.architect,
            status=models.TaskStatus.ready,
            agent_id=agent_id,
            worker_prompt={"prompt": task_data.get("worker_prompt", "")},
            qa_prompt={"prompt": task_data.get("qa_prompt", "")},
            branch_name=active_phase.branch_name,
        )
        db.add(new_task)
        await db.flush()
        actions.append({"type": "task_created", "task_id": str(new_task.id), "title": new_task.title})
        logger.info("agent_chat_task_created", task_id=str(new_task.id), title=new_task.title, agent_id=str(agent_id))

    return actions


async def _execute_goal_markers(
    text: str, project_id: uuid.UUID, db: AsyncSession
) -> list[dict[str, Any]]:
    """Parse [SET_GOAL] markers and create/update project goals."""
    actions: list[dict[str, Any]] = []

    for match in SET_GOAL_RE.finditer(text):
        try:
            goal_data = json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            logger.warning("agent_chat_invalid_goal_marker", raw=match.group(1)[:200])
            continue

        title = goal_data.get("title", "").strip()
        if not title:
            continue

        level = goal_data.get("level", "project")
        description = goal_data.get("description")

        # Upsert: update existing project-level goal or create new one
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
            logger.info("agent_chat_goal_updated", goal_id=str(existing.id), title=title)
        else:
            new_goal = models.Goal(
                tenant_id=DEFAULT_TENANT_ID,
                level=level,
                title=title,
                description=description,
                project_id=project_id,
            )
            db.add(new_goal)
            await db.flush()
            actions.append({"type": "goal_created", "goal_id": str(new_goal.id), "title": title})
            logger.info("agent_chat_goal_created", goal_id=str(new_goal.id), title=title)

    return actions


async def _call_agent(
    agent: models.Agent,
    project: models.Project,
    project_id: uuid.UUID,
    history: list[dict[str, Any]],
    user_message: str,
    db: AsyncSession,
) -> ChatMessageOut:
    """Call a single agent's LLM and process response markers."""
    llm_config = await _resolve_llm_config(agent, db)

    # Build goal context
    goal_context = ""
    goal_svc = GoalAlignmentService(db)
    goal_result = await db.execute(
        select(models.Goal).where(
            models.Goal.project_id == project_id,
            models.Goal.level == "project",
        ).limit(1)
    )
    project_goal = goal_result.scalar_one_or_none()
    if project_goal:
        goal_context = await goal_svc.build_goal_context(project_goal.id)

    # Build LLM messages
    system_prompt = _build_system_prompt(agent, project, goal_context)
    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    for msg in history:
        # Include agent name in assistant messages for context
        content = msg["content"]
        if msg["role"] == "assistant" and msg.get("agent_name"):
            content = f"[{msg['agent_name']}] {content}"
        messages.append({"role": msg["role"], "content": content})
    messages.append({"role": "user", "content": user_message})

    # Call LLM
    client = LLMClient(llm_config)
    try:
        response_text = await client.chat(messages)
    except Exception as e:
        logger.error("agent_chat_llm_error", error=str(e), agent_id=str(agent.id))
        raise HTTPException(status_code=502, detail=f"LLM error: {e}") from e

    # Execute markers
    task_actions = await _execute_create_task_markers(response_text, project_id, agent.id, db)
    goal_actions = await _execute_goal_markers(response_text, project_id, db)
    all_actions = task_actions + goal_actions

    # Clean response
    cleaned_text = _strip_all_markers(response_text)

    # Store assistant message
    msg = _add_message(
        project_id, "assistant", cleaned_text,
        agent_id=agent.id, agent_name=agent.name, actions=all_actions,
    )
    return ChatMessageOut(**msg)


# ── Endpoints ───────────────────────────────────────────────────────


@router.post("", response_model=ChatResponse)
async def chat_with_agent(
    project_id: uuid.UUID,
    body: ChatRequest,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_update)),
    db: AsyncSession = Depends(get_db),
) -> ChatResponse:
    """Send a message to the project chat. @mention agents to direct the conversation."""
    # Load project with phases + tasks
    result = await db.execute(
        select(models.Project)
        .where(models.Project.id == project_id)
        .options(selectinload(models.Project.phases).selectinload(models.Phase.tasks))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Load all agents
    result = await db.execute(
        select(models.Agent).where(models.Agent.tenant_id == DEFAULT_TENANT_ID, models.Agent.is_active.is_(True))
    )
    all_agents = list(result.scalars().all())
    if not all_agents:
        raise HTTPException(status_code=400, detail="No agents configured. Create agents in the Agents page first.")

    # Parse @mentions
    mentioned = _parse_mentions(body.message, all_agents)
    if not mentioned:
        default = _pick_default_agent(all_agents)
        if default:
            mentioned = [default]

    # Store user message
    history = _get_history(project_id)
    _add_message(project_id, "user", body.message)

    # Call each mentioned agent sequentially
    responses: list[ChatMessageOut] = []
    for agent in mentioned:
        msg = await _call_agent(agent, project, project_id, history, body.message, db)
        responses.append(msg)

    await db.commit()
    return ChatResponse(messages=responses)


@router.get("", response_model=ChatHistoryResponse)
async def get_chat_history(
    project_id: uuid.UUID,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_read)),
) -> ChatHistoryResponse:
    """Get unified chat history for a project."""
    history = _get_history(project_id)
    return ChatHistoryResponse(
        messages=[ChatMessageOut(**msg) for msg in history],
    )


@router.delete("")
async def clear_chat_history(
    project_id: uuid.UUID,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_update)),
) -> dict[str, str]:
    """Clear chat history for a project."""
    key = _history_key(project_id)
    _chat_histories.pop(key, None)
    return {"detail": "Chat history cleared"}
