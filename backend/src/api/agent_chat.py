"""Agent Chat API — conversational interface with project agents.

Agents respond using their system prompt + project context.
Responses may contain [CREATE_TASK]...[/CREATE_TASK] markers that auto-create tasks.
"""

from __future__ import annotations

import collections
import json
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
from backend.src.api.settings import get_raw_llm_config
from backend.src.repositories.phase_repository import PhaseRepository
from backend.src.storage.database import get_db

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/projects/{project_id}/chat", tags=["agent-chat"])

# Max conversations kept in memory before oldest are evicted
_MAX_CONVERSATIONS = 200
# Max messages per conversation
_MAX_MESSAGES_PER_CONVERSATION = 100


# ── Request / Response schemas ──────────────────────────────────────


class ChatRequest(BaseModel):
    agent_id: uuid.UUID
    message: str = Field(..., min_length=1, max_length=10000)


class ChatMessageOut(BaseModel):
    id: str
    role: str
    content: str
    created_at: str
    actions: list[dict[str, Any]] = Field(default_factory=list)


class ChatResponse(BaseModel):
    message: ChatMessageOut


class ChatHistoryResponse(BaseModel):
    messages: list[ChatMessageOut]


# ── In-memory chat history (per project+agent, LRU-bounded) ────────

_chat_histories: collections.OrderedDict[str, list[dict[str, Any]]] = collections.OrderedDict()


def _history_key(project_id: uuid.UUID, agent_id: uuid.UUID) -> str:
    return f"{project_id}:{agent_id}"


def _get_history(project_id: uuid.UUID, agent_id: uuid.UUID) -> list[dict[str, Any]]:
    key = _history_key(project_id, agent_id)
    if key in _chat_histories:
        _chat_histories.move_to_end(key)
        return _chat_histories[key]
    history: list[dict[str, Any]] = []
    _chat_histories[key] = history
    # Evict oldest conversations if over limit
    while len(_chat_histories) > _MAX_CONVERSATIONS:
        _chat_histories.popitem(last=False)
    return history


def _add_message(
    project_id: uuid.UUID, agent_id: uuid.UUID, role: str, content: str, *, actions: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    history = _get_history(project_id, agent_id)
    msg = {
        "id": str(uuid.uuid4()),
        "role": role,
        "content": content,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "actions": actions or [],
    }
    history.append(msg)
    # Trim old messages
    if len(history) > _MAX_MESSAGES_PER_CONVERSATION:
        del history[: len(history) - _MAX_MESSAGES_PER_CONVERSATION]
    return msg


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
        '"task_type": "feature", "worker_prompt": "...", "qa_prompt": "..."}[/CREATE_TASK]\n'
        "Only include these markers when the user explicitly asks you to create tasks."
    )

    return "\n\n".join(parts)


async def _resolve_llm_config(agent: models.Agent, db: AsyncSession) -> LLMConfig:
    """Resolve LLM config: agent executor config → global settings → default model."""
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


# ── Endpoints ───────────────────────────────────────────────────────


@router.post("", response_model=ChatResponse)
async def chat_with_agent(
    project_id: uuid.UUID,
    body: ChatRequest,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_update)),
    db: AsyncSession = Depends(get_db),
) -> ChatResponse:
    """Send a message to an agent and get a response. May auto-create tasks."""
    # Load project with phases + tasks
    result = await db.execute(
        select(models.Project)
        .where(models.Project.id == project_id)
        .options(selectinload(models.Project.phases).selectinload(models.Phase.tasks))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Load agent
    result = await db.execute(select(models.Agent).where(models.Agent.id == body.agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Resolve LLM config
    llm_config = await _resolve_llm_config(agent, db)

    # Build goal context (if project has a goal)
    goal_context = ""
    goal_svc = GoalAlignmentService(db)
    goal_result = await db.execute(
        select(models.Goal).where(
            models.Goal.level == "project",
            models.Goal.title.ilike(f"%{project.name}%"),
        ).limit(1)
    )
    project_goal = goal_result.scalar_one_or_none()
    if project_goal:
        goal_context = await goal_svc.build_goal_context(project_goal.id)

    # Build messages
    system_prompt = _build_system_prompt(agent, project, goal_context)
    history = _get_history(project_id, body.agent_id)

    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": body.message})

    # Store user message
    _add_message(project_id, body.agent_id, "user", body.message)

    # Call LLM
    client = LLMClient(llm_config)
    try:
        response_text = await client.chat(messages)
    except Exception as e:
        logger.error("agent_chat_llm_error", error=str(e), agent_id=str(body.agent_id))
        raise HTTPException(status_code=502, detail=f"LLM error: {e}") from e

    # Execute action markers
    actions = await _execute_create_task_markers(response_text, project_id, body.agent_id, db)
    await db.commit()

    # Clean response for user display
    cleaned_text = strip_action_markers(response_text)

    # Store assistant message
    assistant_msg = _add_message(project_id, body.agent_id, "assistant", cleaned_text, actions=actions)

    return ChatResponse(message=ChatMessageOut(**assistant_msg))


@router.get("", response_model=ChatHistoryResponse)
async def get_chat_history(
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_read)),
) -> ChatHistoryResponse:
    """Get chat history for a project + agent pair."""
    history = _get_history(project_id, agent_id)
    return ChatHistoryResponse(
        messages=[ChatMessageOut(**msg) for msg in history],
    )


@router.delete("")
async def clear_chat_history(
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_update)),
) -> dict[str, str]:
    """Clear chat history for a project + agent pair."""
    key = _history_key(project_id, agent_id)
    _chat_histories.pop(key, None)
    return {"detail": "Chat history cleared"}
