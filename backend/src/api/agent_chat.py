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
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sse_starlette.sse import EventSourceResponse

from backend.src import models
from backend.src.api.settings import get_raw_llm_config
from backend.src.core.task_markers import (
    has_claim_marker,
    parse_inline_complete_markers,
    parse_inline_phase_markers,
    parse_inline_task_markers,
    strip_action_markers,
)
from backend.src.core.auth import Permission, require_permission
from backend.src.core.budget import BudgetService
from backend.src.core.executor.litellm_executor import LiteLLMExecutor
from backend.src.core.llm_client import LLMConfig
from backend.src.core.tenant_context import get_tenant_id
from backend.src.core.worker_dispatch import WorkerDispatcher
from backend.src.queue.streams import RedisStreamManager
from backend.src.repositories.conversation_repository import ConversationRepository
from backend.src.storage.database import async_session, get_db
from backend.src.tools.agent_tools import get_tools_for_agent, get_tools_for_mode
from backend.src.tools.approval import ApprovalMiddleware
from backend.src.tools.base import ToolContext
from backend.src.tools.handler import ToolHandler

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/projects/{project_id}/chat", tags=["agent-chat"])

SET_GOAL_RE = re.compile(r"\[SET_GOAL\](.*?)\[/SET_GOAL\]", re.DOTALL)
STATUS_RE = re.compile(r"^\[STATUS\]\s*(.+?)$", re.MULTILINE)
DECISION_RE = re.compile(r"\[DECISION\](.*?)\[/DECISION\]", re.DOTALL)

# Track background agent tasks per project for cancellation.
_project_tasks: dict[uuid.UUID, set[asyncio.Task]] = {}
MAX_HISTORY = 20
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
    task_id: uuid.UUID | None = None
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



async def _pick_next_handoff_agent(
    *,
    project_id: uuid.UUID,
    current_agent_id: uuid.UUID,
    all_agents: list[models.Agent],
    completed_task_id: uuid.UUID | None,
) -> models.Agent | None:
    """Choose who should be @mentioned when a passive agent forgets to hand off.

    Priority:
      1. The assignee of the next pending task in the active phase (keeps
         the same phase moving).
      2. A pending task's assignee anywhere in the project (avoid stalling).
      3. The org root (CEO) — so they can plan the next step.
    """
    async with async_session() as db:
        from backend.src.models import Task, TaskStatus, Phase, PhaseStatus

        # 1. Same-phase pending task assignees
        completed_phase_id = None
        if completed_task_id is not None:
            done_task = await db.get(Task, completed_task_id)
            if done_task is not None:
                completed_phase_id = done_task.phase_id

        if completed_phase_id is not None:
            stmt = (
                select(Task)
                .where(
                    Task.phase_id == completed_phase_id,
                    Task.status == TaskStatus.pending,
                    Task.assigned_agent_id.isnot(None),
                    Task.assigned_agent_id != current_agent_id,
                )
                .order_by(Task.created_at.asc())
                .limit(1)
            )
            result = await db.execute(stmt)
            nxt = result.scalar_one_or_none()
            if nxt and nxt.assigned_agent_id:
                found = next((a for a in all_agents if a.id == nxt.assigned_agent_id), None)
                if found is not None:
                    return found

        # 2. Next phase's assignees (if active phase is all done, look ahead)
        active_phase_stmt = (
            select(Phase)
            .where(Phase.project_id == project_id, Phase.status == PhaseStatus.active)
            .order_by(Phase.order.asc())
            .limit(1)
        )
        result = await db.execute(active_phase_stmt)
        active_phase = result.scalar_one_or_none()
        if active_phase is not None:
            stmt = (
                select(Task)
                .where(
                    Task.phase_id == active_phase.id,
                    Task.status == TaskStatus.pending,
                    Task.assigned_agent_id.isnot(None),
                    Task.assigned_agent_id != current_agent_id,
                )
                .order_by(Task.created_at.asc())
                .limit(1)
            )
            result = await db.execute(stmt)
            nxt = result.scalar_one_or_none()
            if nxt and nxt.assigned_agent_id:
                found = next((a for a in all_agents if a.id == nxt.assigned_agent_id), None)
                if found is not None:
                    return found

    # 3. Fallback to the org root so they can plan what's next
    root = _find_org_root(all_agents)
    if root is not None and root.id != current_agent_id:
        return root
    return None


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
    *,
    tenant_id: uuid.UUID,
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
    worker = await dispatcher.find_available_worker(db, tenant_id=tenant_id)
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
    all_agents: list[models.Agent],
    org_context: str = "",
    active_decisions: list[str] | None = None,
    mode: str = "active",
    task_context: str = "",
) -> str:
    """Build the system prompt via the harness (workspace-based modules)."""
    from backend.src.core.harness import assemble_system_prompt, seed_harness

    workspace_dir = project.workspace_dir
    if workspace_dir:
        seed_harness(workspace_dir)

    return await assemble_system_prompt(
        agent, project, workspace_dir,
        mode=mode,
        task_context=task_context,
        org_context=org_context,
        all_agents=all_agents,
        active_decisions=active_decisions,
    )


def _strip_all_markers(text: str) -> str:
    text = strip_action_markers(text)
    text = SET_GOAL_RE.sub("", text).strip()
    text = STATUS_RE.sub("", text).strip()
    text = DECISION_RE.sub("", text).strip()
    # Strip Qwen3 thinking tags (leaked from reasoning mode)
    text = re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL).strip()
    return re.sub(r"^\[.*?\]\s*", "", text, count=1)



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



# ── Persistence + event publishing ──────────────────────────────────


def _message_to_event(msg: models.ConversationMessage) -> dict[str, Any]:
    return {
        "id": str(msg.id),
        "role": msg.role,
        "content": msg.content,
        "agent_id": str(msg.agent_id) if msg.agent_id else None,
        "agent_name": msg.agent_name,
        "actions": msg.actions or [],
        "task_id": str(msg.task_id) if msg.task_id else None,
        "created_at": msg.created_at.isoformat(),
    }


async def _publish_event(redis: Any, project_id: uuid.UUID, event: str, data: dict[str, Any]) -> None:
    if redis is None:
        return
    stream_manager = RedisStreamManager(redis)
    stream = RedisStreamManager.chat_events_stream(str(project_id))
    await stream_manager.publish(stream, {"event": event, "data": data})

    # Maintain transient Redis key for agent_processing state so the
    # agents API can report it even if SSE was missed.
    if event == "agent_processing":
        agent_id = data.get("agent_id", "")
        key = f"agent:processing:{agent_id}"
        if data.get("status") == "started":
            await redis.set(key, str(project_id), ex=600)  # 10 min TTL
        else:
            await redis.delete(key)
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
    task_id: uuid.UUID | None = None,
) -> models.ConversationMessage:
    repo = ConversationRepository(db)
    msg = await repo.append(
        project_id, role=role, content=content,
        agent_id=agent.id if agent else None,
        agent_name=agent.name if agent else None,
        actions=actions, source=source,
        task_id=task_id,
    )
    await db.commit()
    await _publish_event(redis, project_id, "message_created", _message_to_event(msg))
    return msg




# ── LLM / worker dispatch ───────────────────────────────────────────


async def _resolve_llm_config(agent: models.Agent, db: AsyncSession) -> LLMConfig:
    """Resolve LLM config from agent's executor config or global DB settings.

    No env-var fallback for model — must be configured per-tenant in DB.
    """
    if agent.executor_config_id:
        result = await db.execute(
            select(models.ExecutorConfig).where(models.ExecutorConfig.id == agent.executor_config_id)
        )
        exec_cfg = result.scalar_one_or_none()
        if exec_cfg and exec_cfg.config and exec_cfg.config.get("model"):
            cfg = exec_cfg.config
            # api_key can be empty for local models (ollama, vllm, etc.)
            # litellm requires a non-empty string even if the server ignores it.
            return LLMConfig(
                api_key=cfg.get("api_key") or "unused",
                model=cfg["model"],
                base_url=cfg.get("base_url"),
            )

    raw = await get_raw_llm_config(db)
    api_key = raw.get("llm_api_key")
    if not api_key:
        raise HTTPException(status_code=400, detail="No LLM API key configured.")
    model = raw.get("llm_model")
    if not model:
        raise HTTPException(status_code=400, detail="No LLM model configured.")
    return LLMConfig(
        api_key=api_key,
        model=model,
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
    mode: str = "active",
    task_context: str = "",
) -> tuple[str, list[dict[str, str]]]:
    org_context = await _build_org_context(tenant_id, db)

    # Load active decisions from .bsnexus/context/decisions.md (file-based,
    # single source of truth — no DB table).
    active_decisions = _load_decisions_from_workspace(project.workspace_dir)

    system_prompt = await _build_system_prompt(
        agent, project, all_agents=all_agents,
        org_context=org_context,
        active_decisions=active_decisions,
        mode=mode,
        task_context=task_context,
    )
    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    for h in history:
        content = h.content
        if h.role == "assistant" and h.agent_name:
            content = f"[{h.agent_name}] {content}"
        messages.append({"role": h.role, "content": content})
    messages.append({"role": "user", "content": user_message})
    return system_prompt, messages


async def _execute_inline_markers(
    text: str,
    *,
    project_id: uuid.UUID,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    agent_name: str,
) -> list[dict[str, Any]]:
    """Parse and execute ``[CREATE_PHASE ...]`` and ``[CREATE_TASK ...]`` inline markers.

    Returns action records in the same format as ``_build_tool_actions()``
    so the frontend invalidation logic works unchanged.
    """
    from backend.src.tools.plan_tools import create_phase_from_params, create_task_from_params

    actions: list[dict[str, Any]] = []

    if not text:
        return actions

    # Phases first — tasks may reference them by name.
    for pm in parse_inline_phase_markers(text):
        try:
            result = await create_phase_from_params(
                name=pm["name"],
                description=pm.get("description") or "",
                project_id=project_id,
                db_session_factory=async_session,
            )
            actions.append({
                "type": "tool_create_phase",
                "tool": "create_phase",
                "input": {"name": pm["name"], "description": pm.get("description") or ""},
            })
            logger.info("phase_created_via_marker", name=pm["name"], result=result.get("status"))
        except Exception:
            logger.warning("marker_phase_creation_failed", name=pm.get("name"), exc_info=True)

    # Tasks — max 10 per response.
    task_markers = parse_inline_task_markers(text)
    created_count = 0
    for tm in task_markers[:10]:
        try:
            result = await create_task_from_params(
                title=tm["title"],
                description=tm.get("description"),
                priority=tm.get("priority") or "medium",
                task_type=tm.get("task_type") or "feature",
                assignee=tm.get("assignee"),
                phase_name=tm.get("phase_name"),
                project_id=project_id,
                tenant_id=tenant_id,
                agent_id=agent_id,
                agent_name=agent_name,
                db_session_factory=async_session,
                tasks_created_count=created_count,
                max_tasks=10,
            )
            if "message" not in result:
                created_count += 1
            actions.append({
                "type": "tool_create_task",
                "tool": "create_task",
                "input": {k: v for k, v in tm.items() if v is not None},
            })
            logger.info("task_created_via_marker", title=tm["title"],
                        assignee=tm.get("assignee"), result_status=result.get("status"))
        except Exception:
            logger.warning("marker_task_creation_failed", title=tm.get("title"), exc_info=True)

    # Claim: [CLAIM_TASK] — find agent's assigned pending/running task
    if has_claim_marker(text):
        try:
            from backend.src.core.state_machine import TaskStateMachine
            from backend.src.models import Task, TaskStatus
            async with async_session() as db:
                result = await db.execute(
                    select(Task).where(
                        Task.project_id == project_id,
                        Task.assigned_agent_id == agent_id,
                        Task.status == TaskStatus.pending,
                    ).order_by(Task.created_at.desc()).limit(1)
                )
                task = result.scalar_one_or_none()
                if task:
                    sm = TaskStateMachine()
                    await sm.transition(
                        task, TaskStatus.running,
                        actor=f"agent:{agent_id}",
                        reason=f"Claimed by {agent_name} via marker",
                        db_session=db,
                    )
                    await db.commit()
                    actions.append({"type": "tool_claim_task", "tool": "claim_task", "input": {"task_id": str(task.id)}})
                    logger.info("task_claimed_via_marker", task_id=str(task.id), agent=agent_name)
        except Exception:
            logger.warning("marker_claim_failed", exc_info=True)

    # Complete: [COMPLETE_TASK summary="..."] — find agent's running task
    for cm in parse_inline_complete_markers(text):
        try:
            from backend.src.core.state_machine import TaskStateMachine
            from backend.src.models import Task, TaskStatus
            async with async_session() as db:
                result = await db.execute(
                    select(Task).where(
                        Task.project_id == project_id,
                        Task.assigned_agent_id == agent_id,
                        Task.status == TaskStatus.running,
                    ).order_by(Task.created_at.desc()).limit(1)
                )
                task = result.scalar_one_or_none()
                if task:
                    summary = cm.get("summary") or f"Completed by {agent_name}"
                    task.output_data = {"summary": summary, "completed_by": agent_name}
                    sm = TaskStateMachine()
                    await sm.transition(
                        task, TaskStatus.done,
                        actor=f"agent:{agent_id}",
                        reason=summary,
                        db_session=db,
                    )
                    await db.commit()
                    actions.append({
                        "type": "tool_complete_task", "tool": "complete_task",
                        "input": {"task_id": str(task.id), "summary": summary},
                    })
                    logger.info("task_completed_via_marker", task_id=str(task.id), agent=agent_name)
        except Exception:
            logger.warning("marker_complete_failed", exc_info=True)

    return actions


async def _process_response_text(
    response_text: str, project: models.Project, project_id: uuid.UUID,
    agent: models.Agent, db: AsyncSession, redis: Any,
    *, tenant_id: uuid.UUID,
    tool_actions: list[dict[str, Any]] | None = None,
    task_id: uuid.UUID | None = None,
) -> models.ConversationMessage:
    """Persist the agent's response and publish it via SSE.

    Inline markers (``[CREATE_TASK ...]``, ``[CREATE_PHASE ...]``) are
    parsed and executed BEFORE stripping. Tool side-effects from the
    agentic loop are already done; this handles the marker-based path.
    """
    # Parse and execute inline markers before stripping
    marker_actions = await _execute_inline_markers(
        response_text or "",
        project_id=project_id,
        tenant_id=tenant_id,
        agent_id=agent.id,
        agent_name=agent.name,
    )
    all_actions = (tool_actions or []) + marker_actions

    cleaned = _strip_all_markers(response_text)
    if (not cleaned.strip() or _looks_like_tool_call_json(cleaned)) and all_actions:
        cleaned = _summarize_actions(all_actions)
    return await _store_and_publish(
        db, redis, project_id, role="assistant", content=cleaned,
        agent=agent, actions=all_actions,
        task_id=task_id,
    )


def _looks_like_tool_call_json(text: str) -> bool:
    """Detect content that is a raw tool-call dump (Qwen3 occasionally emits
    the function-call object — or a tool's arguments — as plain text instead
    of using the tool_calls slot). Keeping such JSON as the visible message
    looks like a log, so we replace it with an action summary."""
    stripped = text.strip()
    if not (stripped.startswith("{") or stripped.startswith("[")):
        return False

    # Whole body parses as JSON — almost always a tool-call / result dump.
    try:
        import json as _json
        _json.loads(stripped)
        return True
    except ValueError:
        pass

    # Otherwise look for signature keys that mark this as a tool payload
    # even if the JSON is truncated or has trailing text.
    markers = (
        '"function"', '"tool_calls"', '"arguments"',
        '"task_id"', '"status"',
        # create_screen / modify_screen / file_write / list_tasks payloads
        '"spec"', '"slug"', '"route":',
        '"path":', '"content":',
        # list_tasks / list_* tool results
        '"tasks":', '"phases":', '"screens":',
    )
    hits = sum(1 for m in markers if m in stripped)
    return hits >= 2  # raise the bar when the body isn't fully valid JSON


def _summarize_actions(actions: list[dict[str, Any]]) -> str:
    """Build a conversational summary from tool calls — used as a fallback
    when the LLM returns only tool_use without natural-language text.

    Shapes the output like a teammate updating the group chat so that the
    project log reads as a conversation rather than a structured event log.
    """
    from collections import Counter

    if not actions:
        return ""

    counts: Counter[str] = Counter()
    phase_names: list[str] = []
    created_tasks: list[tuple[str, str | None]] = []  # (title, assignee)
    completed_task_titles: list[str] = []
    completed_summaries: list[str] = []  # full completion summary text for @mention extraction
    files_written: list[str] = []
    blocked_count = 0

    for a in actions:
        tool = a.get("tool", "")
        counts[tool] += 1
        inp = a.get("input", {}) or {}
        if tool == "create_phase":
            name = inp.get("name")
            if isinstance(name, str) and name and name not in phase_names:
                phase_names.append(name)
        elif tool == "create_task":
            title = inp.get("title")
            assignee = inp.get("assignee")
            if isinstance(title, str) and title:
                created_tasks.append((title, assignee if isinstance(assignee, str) else None))
        elif tool == "complete_task":
            summary = inp.get("summary")
            if isinstance(summary, str) and summary:
                completed_task_titles.append(summary.split("\n")[0].strip())
                completed_summaries.append(summary)
        elif tool == "file_write":
            path = inp.get("path")
            if isinstance(path, str) and path and len(files_written) < 8:
                files_written.append(path)
        elif tool == "update_task":
            if inp.get("status") == "blocked":
                blocked_count += 1

    # Deduplicate while preserving order
    def _dedup(seq: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for s in seq:
            if s not in seen:
                seen.add(s)
                out.append(s)
        return out

    lines: list[str] = []

    # ── Planner voice (phase + task creation) ──
    if phase_names or created_tasks:
        if phase_names:
            if len(phase_names) == 1:
                lines.append(f"'{phase_names[0]}' 단계를 열었어요.")
            else:
                lines.append(f"단계 {len(phase_names)}개를 열었어요: {', '.join(phase_names)}.")
        if created_tasks:
            unique_titles = _dedup([t for t, _ in created_tasks])
            lines.append(f"작업 {len(unique_titles)}개를 만들었습니다:")
            for title, _ in created_tasks[:6]:
                lines.append(f"  • {title}")
            if len(created_tasks) > 6:
                lines.append(f"  • (외 {len(created_tasks) - 6}건)")
            assignees = _dedup([a for _, a in created_tasks if a])
            if assignees:
                mentions = " ".join(f"@{a}" for a in assignees[:5])
                lines.append(f"{mentions} — 각자 맡은 작업 확인해주세요. 완료되면 다음 단계로 넘어갈게요.")

    # ── Executor voice (claim → file_write → complete) ──
    if files_written:
        if len(files_written) == 1:
            lines.append(f"`{files_written[0]}` 파일을 작성했어요.")
        else:
            lines.append(f"다음 파일들을 작성했어요: {', '.join(f'`{p}`' for p in files_written)}.")
    if completed_task_titles:
        first = completed_task_titles[0]
        more = len(completed_task_titles) - 1
        if more > 0:
            lines.append(f"작업 완료: {first} (외 {more}건).")
        else:
            lines.append(f"작업 완료: {first}")
    elif counts.get("complete_task") and not files_written:
        # complete_task was called but no summary captured
        lines.append(f"할당된 작업 {counts['complete_task']}건을 마쳤습니다.")

    # ── Preserve @mentions from completion summaries ──
    # When a passive agent wrote "@Designer 이어서 부탁" inside complete_task.summary,
    # surface that handoff at the end of the fallback message so ping-pong
    # delegation (passive → active) can pick up those mentions.
    if completed_summaries:
        import re as _re
        mention_pat = _re.compile(r"@([A-Za-z][A-Za-z0-9_-]*)")
        seen_mentions: list[str] = []
        for s in completed_summaries:
            for m in mention_pat.findall(s):
                if m not in seen_mentions:
                    seen_mentions.append(m)
        if seen_mentions:
            lines.append("➡ " + " ".join(f"@{m}" for m in seen_mentions[:5]) + " — 이어서 부탁드립니다.")

    # ── Blocker voice ──
    if blocked_count:
        lines.append(f"⚠️ {blocked_count}건은 진행이 막혀서 blocked 처리했어요 — 확인이 필요합니다.")

    # ── Last resort ──
    if not lines:
        tool_list = ", ".join(f"{name}×{n}" for name, n in counts.most_common(5))
        lines.append(f"도구 호출: {tool_list}")

    return "\n".join(lines)


async def _call_via_executor(
    agent: models.Agent, project: models.Project, project_id: uuid.UUID,
    history: list[models.ConversationMessage], user_message: str,
    db: AsyncSession | None, redis: Any, all_agents: list[models.Agent],
    *, tenant_id: uuid.UUID,
    mode: str = "active",
    task_context: str = "",
) -> models.ConversationMessage:
    """Call the agent via LiteLLMExecutor with tool_use support."""
    # Phase A: Build context (short-lived DB session).
    async with async_session() as setup_db:
        llm_config = await _resolve_llm_config(agent, setup_db)
        _, messages = await _build_chat_context(
            agent, project, project_id, history, user_message, setup_db, all_agents,
            tenant_id=tenant_id, mode=mode, task_context=task_context,
        )
    # setup_db CLOSED — no DB held during executor run.

    # Phase B: Build tool context and handler.
    tools = get_tools_for_mode(mode, agent.capabilities)
    stream_manager = RedisStreamManager(redis) if redis else None
    tool_context = ToolContext(
        project_id=project_id,
        workspace_path=(
            __import__("pathlib").Path(project.workspace_dir)
            if project.workspace_dir
            else __import__("pathlib").Path(
                __import__("os").environ.get("WORKSPACE_BASE_DIR", "./data/workspaces")
            ) / str(project_id)
        ),
        workspace_type=(
            project.workspace_type.value
            if hasattr(project.workspace_type, "value")
            else str(project.workspace_type)
        ),
        agent_id=agent.id,
        agent_name=agent.name,
        tenant_id=tenant_id,
        db_session_factory=async_session,
        redis=redis,
        stream_manager=stream_manager,
    )
    approval = ApprovalMiddleware()
    tool_handler = ToolHandler(tools, tool_context, approval=approval)

    # Phase C: Run agentic loop (no DB held).
    executor = LiteLLMExecutor()
    result = await executor.execute(
        messages=messages,
        tools=[t.to_definition() for t in tools] if tools else None,
        tool_handler=tool_handler,
        model=llm_config.model,
        api_key=llm_config.api_key,
        base_url=llm_config.base_url,
        project_id=project_id,
        on_event=lambda e: _publish_tool_event(redis, project_id, e),
    )

    # Phase D: Record cost + persist response (short-lived DB session).
    async with async_session() as result_db:
        import math
        cost_cents = math.ceil(result.cost_usd * 100)
        if cost_cents > 0:
            budget_svc = BudgetService(result_db)
            await budget_svc.record_cost(
                tenant_id=tenant_id,
                agent_id=agent.id,
                amount_cents=cost_cents,
                token_count=result.total_tokens,
                model_name=result.model,
            )

        # Build tool actions summary for SSE
        tool_actions = _build_tool_actions(result)
        primary_task_id = _extract_primary_task_id(result)

        # Build delegation text from tool call inputs (for @mention parsing).
        # When agents use tool_use only (no text response), mentions live
        # in tool arguments (e.g. task descriptions, file content).
        delegation_text_parts = [result.content or ""]
        for tc in (result.tool_calls_made or []):
            if isinstance(tc.input, dict):
                for v in tc.input.values():
                    if isinstance(v, str):
                        delegation_text_parts.append(v)

        msg = await _process_response_text(
            result.content, project, project_id, agent, result_db, redis,
            tenant_id=tenant_id, tool_actions=tool_actions,
            task_id=primary_task_id,
        )
        # Attach delegation text for _process_agent_in_background
        msg._delegation_text = " ".join(delegation_text_parts)  # type: ignore[attr-defined]
        return msg


def _build_tool_actions(result: Any) -> list[dict[str, Any]]:
    """Convert executor tool audit trail to action dicts for SSE."""
    actions: list[dict[str, Any]] = []
    for tc in (result.tool_calls_made or []):
        actions.append({
            "type": f"tool_{tc.name}",
            "tool": tc.name,
            "input": tc.input,
        })
    return actions


def _extract_primary_task_id(result: Any) -> uuid.UUID | None:
    """Find the task this agent turn was primarily about.

    Only reads from **successful tool results** — never from tool call
    inputs, because the LLM can hallucinate UUIDs that don't exist in DB.
    """
    import json as _json

    for tr in reversed(result.tool_results or []):
        if tr.is_error:
            continue
        try:
            data = _json.loads(tr.content)
            if data.get("task_id"):
                return uuid.UUID(data["task_id"])
        except (ValueError, KeyError, TypeError, _json.JSONDecodeError):
            continue
    return None


async def _publish_tool_event(redis: Any, project_id: uuid.UUID, event: Any) -> None:
    """Publish tool execution events to SSE for frontend visibility."""
    if redis is None or event is None:
        return
    try:
        await _publish_event(redis, project_id, event.type, event.data)
    except Exception:
        pass  # Best-effort — don't break the loop


async def _call_via_worker(
    agent: models.Agent, project: models.Project, project_id: uuid.UUID,
    history: list[models.ConversationMessage], user_message: str,
    db: AsyncSession | None, redis: Any, all_agents: list[models.Agent],
    *, tenant_id: uuid.UUID,
) -> models.ConversationMessage:
    """Dispatch to a worker. Opens short-lived DB sessions for setup and
    result processing so no connection is held during the long poll."""
    if redis is None:
        raise HTTPException(status_code=500, detail="Redis not available for worker dispatch")

    # Phase A: short-lived session for worker lookup + prompt build.
    async with async_session() as setup_db:
        worker_id: uuid.UUID | None = None
        if agent.executor_config_id:
            result = await setup_db.execute(
                select(models.ExecutorConfig).where(models.ExecutorConfig.id == agent.executor_config_id)
            )
            exec_cfg = result.scalar_one_or_none()
            if exec_cfg and exec_cfg.config.get("worker_id"):
                worker_id = uuid.UUID(exec_cfg.config["worker_id"])

        stream_manager = RedisStreamManager(redis)
        dispatcher = WorkerDispatcher(stream_manager)

        if worker_id:
            result = await setup_db.execute(
                select(models.Worker).where(models.Worker.id == worker_id, models.Worker.is_active.is_(True))
            )
            worker = result.scalar_one_or_none()
            if not worker or worker.status != "online":
                raise HTTPException(status_code=503, detail="현재 오프라인 상태입니다.")
        else:
            worker = await dispatcher.find_available_worker(setup_db, tenant_id=tenant_id)
            if not worker:
                raise HTTPException(status_code=503, detail="현재 오프라인 상태입니다.")

        system_prompt, _ = await _build_chat_context(
            agent, project, project_id, history, user_message, setup_db, all_agents, tenant_id=tenant_id,
        )
    # setup_db CLOSED — connection returned to pool.
    flat_history: list[dict[str, str]] = []
    for h in history:
        content = h.content
        if h.role == "assistant" and h.agent_name:
            content = f"[{h.agent_name}] {content}"
        flat_history.append({"role": h.role, "content": content})

    # Include tool definitions so the worker can run an agentic loop.
    tools = get_tools_for_agent(agent.capabilities)
    tool_defs = [t.to_definition().to_dict() for t in tools] if tools else None
    tool_names = [t.name for t in tools] if tools else None

    chat_id = str(uuid.uuid4())
    await dispatcher.dispatch_chat(
        worker_id=worker.id, chat_id=chat_id, message=user_message,
        system_prompt=system_prompt, history=flat_history,
        tool_definitions=tool_defs,
        agent_tool_names=tool_names,
    )

    # Poll for the result WITHOUT holding the DB session — the polling
    # loop only touches Redis. Once the result arrives, open a fresh
    # session for persistence. This prevents long-running worker turns
    # (up to 30 min) from exhausting the connection pool.
    result_key = f"chat:result:{chat_id}"
    waited = 0.0
    while waited < WORKER_RESULT_TIMEOUT:
        raw = await redis.get(result_key)
        if raw:
            await redis.delete(result_key)
            payload = json.loads(raw)
            if not payload.get("success", False):
                raise HTTPException(status_code=502, detail=f"Worker error: {payload.get('error_message', 'failed')}")

            # Extract tool audit trail from worker result (if present).
            tool_actions: list[dict[str, Any]] = []
            for tc in payload.get("tool_calls", []):
                tool_actions.append({
                    "type": f"tool_{tc.get('name', 'unknown')}",
                    "tool": tc.get("name"),
                    "input": tc.get("input", {}),
                })

            # Record cost from worker result.
            usage = payload.get("usage", {})
            cost_cents = usage.get("cost_cents", 0)

            # Build delegation text from tool call inputs (worker path).
            delegation_parts = [payload.get("output", "")]
            for tc in tool_actions:
                inp = tc.get("input", {})
                if isinstance(inp, dict):
                    for v in inp.values():
                        if isinstance(v, str):
                            delegation_parts.append(v)
            delegation_text = " ".join(delegation_parts)

            async with async_session() as fresh_db:
                if cost_cents > 0:
                    budget_svc = BudgetService(fresh_db)
                    await budget_svc.record_cost(
                        tenant_id=tenant_id,
                        agent_id=agent.id,
                        amount_cents=cost_cents,
                        token_count=usage.get("total_tokens", 0),
                        model_name=usage.get("model", ""),
                    )
                msg = await _process_response_text(
                    payload.get("output", ""), project, project_id, agent, fresh_db, redis,
                    tenant_id=tenant_id, tool_actions=tool_actions,
                )
                msg._delegation_text = delegation_text  # type: ignore[attr-defined]
                return msg
        await asyncio.sleep(0.5)
        waited += 0.5

    raise HTTPException(status_code=504, detail=f"Worker timed out ({int(WORKER_RESULT_TIMEOUT)}s).")


async def _call_agent(
    agent: models.Agent, project: models.Project, project_id: uuid.UUID,
    history: list[models.ConversationMessage], user_message: str,
    db: AsyncSession | None, redis: Any, all_agents: list[models.Agent],
    *, tenant_id: uuid.UUID,
    mode: str = "active",
    task_context: str = "",
) -> models.ConversationMessage:
    """Route to executor; fall back to worker if LLM is unconfigured."""
    if agent.executor_type == "worker":
        return await _call_via_worker(
            agent, project, project_id, history, user_message, db, redis, all_agents,
            tenant_id=tenant_id,
        )
    try:
        return await _call_via_executor(
            agent, project, project_id, history, user_message, db, redis, all_agents,
            tenant_id=tenant_id, mode=mode, task_context=task_context,
        )
    except HTTPException as e:
        if e.status_code == 400 and "No LLM API key" in str(e.detail):
            stream_manager = RedisStreamManager(redis) if redis else None
            dispatcher = WorkerDispatcher(stream_manager) if stream_manager else None
            if dispatcher:
                async with async_session() as fallback_db:
                    worker = await dispatcher.find_available_worker(fallback_db, tenant_id=tenant_id)
            else:
                worker = None
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
    agent: models.Agent | None = None

    # Phase 1: short-lived DB session for setup. Do NOT mark busy yet —
    # we first check that an executor is actually available so the UI
    # doesn't show a green dot for an agent that will immediately 503.
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
            return

        # Check executor availability BEFORE marking busy.
        if agent.executor_type == "worker":
            stream_manager = RedisStreamManager(redis) if redis else None
            dispatcher = WorkerDispatcher(stream_manager) if stream_manager else None
            worker = await dispatcher.find_available_worker(setup_db, tenant_id=tenant_id) if dispatcher else None
            if not worker:
                # No worker online — publish error immediately, don't mark busy.
                await _store_and_publish(
                    setup_db, redis, project_id,
                    role="assistant",
                    content="현재 오프라인 상태입니다. 잠시 후 다시 시도해 주세요.",
                    agent=agent,
                )
                return
        else:
            # LLM executor — check if API key is configured.
            try:
                await _resolve_llm_config(agent, setup_db)
            except HTTPException:
                # No LLM key — check if a worker fallback is available.
                stream_manager = RedisStreamManager(redis) if redis else None
                dispatcher = WorkerDispatcher(stream_manager) if stream_manager else None
                worker = await dispatcher.find_available_worker(setup_db, tenant_id=tenant_id) if dispatcher else None
                if not worker:
                    await _store_and_publish(
                        setup_db, redis, project_id,
                        role="assistant",
                        content="현재 오프라인 상태입니다. 잠시 후 다시 시도해 주세요.",
                        agent=agent,
                    )
                    return

        history = await ConversationRepository(setup_db).list_by_project(project_id, limit=MAX_HISTORY)
    # setup_db is now CLOSED — connection returned to pool.

    # Publish agent_processing started event for frontend busy indicator.
    await _publish_event(redis, project_id, "agent_processing", {
        "agent_id": str(agent_id),
        "agent_name": agent.name,
        "status": "started",
        "mode": "active",
    })

    # Phase 2: call agent (may block for minutes on worker polling).
    # Each internal function opens its own short-lived session so no
    # connection is held during the long worker poll.
    try:
        msg = await _call_agent(
            agent, project, project_id, history, user_message, None, redis, all_agents,
            tenant_id=tenant_id,
        )

        # Delegation: dispatch @mentioned agents OR auto-delegate pending tasks.
        # Two strategies:
        # 1. Parse @mentions from text + tool call args
        # 2. If no mentions found, auto-delegate: find pending unclaimed tasks
        #    and dispatch the org-root (CEO) to handle them
        delegation_text = getattr(msg, "_delegation_text", None) or msg.content or ""

        delegated = _parse_mentions(delegation_text, all_agents)
        delegated = [d for d in delegated if d.id != agent_id]

        # Auto-delegation fallback: if the agent created tasks but didn't
        # @mention anyone (common with tool_use-only models like Qwen3),
        # dispatch the org root (CEO) to review and delegate the new tasks.
        if not delegated and msg.actions:
            created_tasks = [a for a in msg.actions if isinstance(a, dict) and a.get("tool") == "create_task"]
            if created_tasks:
                org_root = _find_org_root(all_agents)
                if org_root and org_root.id != agent_id:
                    delegated = [org_root]
                    logger.info("auto_delegation_to_root",
                                from_agent=str(agent_id),
                                root_agent=org_root.name,
                                tasks_created=len(created_tasks))

        if delegated:
            logger.info("delegation_triggered",
                        from_agent=str(agent_id),
                        to_agents=[d.name for d in delegated])
        from backend.src.core.agent_queue import AgentRequest, get_agent_queue_manager
        mgr = get_agent_queue_manager()
        for delegate in delegated:
            delegation_msg = msg.content or "새로운 작업이 생성되었습니다. list_tasks로 확인하고 적절한 팀원에게 업무를 배분해주세요."
            await mgr.enqueue(AgentRequest(
                mode="active",
                project_id=project_id,
                agent_id=delegate.id,
                tenant_id=tenant_id,
                redis=redis,
                message=delegation_msg,
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
                    role="assistant", content="죄송합니다, 요청을 처리하는 중 문제가 발생했습니다. 잠시 후 다시 시도해 주세요.",
                    agent=error_agent,
                )
        except Exception:  # noqa: BLE001
            pass
    finally:
        try:
            await _publish_event(redis, project_id, "agent_processing", {
                "agent_id": str(agent_id),
                "agent_name": agent.name if agent else "",
                "status": "completed",
                "mode": "active",
            })
        except Exception:
            pass


# ── Passive mode agent processing ──────────────────────────────────


async def _process_agent_in_background_passive(
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    task_id: uuid.UUID,
    task_context: str,
    redis: Any,
    tenant_id: uuid.UUID,
) -> None:
    """Run an agent in passive mode to execute a specific task.

    Passive agents:
    - Get execution tools only (claim, file_write, complete)
    - Receive task details in the system prompt
    - After completion, if their chat message @mentions another agent,
      that agent is dispatched in active mode so the delegation chain
      continues across task boundaries (ping-pong collaboration).
    """
    async with async_session() as setup_db:
        project_result = await setup_db.execute(
            select(models.Project).where(models.Project.id == project_id)
            .options(selectinload(models.Project.phases).selectinload(models.Phase.tasks))
        )
        project = project_result.scalar_one_or_none()
        if not project:
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
            return

        history = await ConversationRepository(setup_db).list_by_project(project_id, limit=MAX_HISTORY)

    # Publish agent_processing started event for frontend busy indicator.
    await _publish_event(redis, project_id, "agent_processing", {
        "agent_id": str(agent_id),
        "agent_name": agent.name,
        "status": "started",
        "mode": "passive",
    })

    user_message = (
        f"작업이 할당되었습니다. 아래 내용을 확인하고 실행해주세요.\n\n"
        f"{task_context}\n\n"
        f"claim_task로 작업을 시작하고, 완료되면 complete_task로 마무리해주세요."
    )

    try:
        msg = await _call_agent(
            agent, project, project_id, history, user_message, None, redis, all_agents,
            tenant_id=tenant_id,
            mode="passive",
            task_context=task_context,
        )
        logger.info("passive_agent_completed", agent=agent.name, task_id=str(task_id))

        # Ping-pong delegation: if the passive agent's final chat message
        # @mentions a teammate, wake that teammate in active mode so the
        # conversation keeps going. Qwen3-class models frequently forget to
        # @mention anyone even when the prompt asks for it, so we also
        # auto-inject a handoff mention when the message has none — picking
        # the next logical teammate from the project state.
        if msg is not None:
            delegation_text = getattr(msg, "_delegation_text", None) or msg.content or ""
            delegated = _parse_mentions(delegation_text, all_agents)
            delegated = [d for d in delegated if d.id != agent_id]

            if not delegated:
                next_agent = await _pick_next_handoff_agent(
                    project_id=project_id,
                    current_agent_id=agent_id,
                    all_agents=all_agents,
                    completed_task_id=task_id,
                )
                if next_agent is not None:
                    augmented = (msg.content or "") + (
                        f"\n\n➡ @{next_agent.name} 이어서 필요한 작업을 맡아주세요."
                    )
                    async with async_session() as upd_db:
                        db_msg = await upd_db.get(models.ConversationMessage, msg.id)
                        if db_msg is not None:
                            db_msg.content = augmented
                            await upd_db.commit()
                    msg.content = augmented  # keep local copy in sync for subsequent use
                    delegated = [next_agent]
                    logger.info(
                        "auto_handoff_mention_injected",
                        from_agent=agent.name,
                        to_agent=next_agent.name,
                        task_id=str(task_id),
                    )

            if delegated:
                from backend.src.core.agent_queue import AgentRequest, get_agent_queue_manager
                mgr = get_agent_queue_manager()
                for delegate in delegated:
                    await mgr.enqueue(AgentRequest(
                        mode="active",
                        project_id=project_id,
                        agent_id=delegate.id,
                        tenant_id=tenant_id,
                        redis=redis,
                        message=msg.content or "동료가 방금 작업을 완료하고 당신을 멘션했습니다. 이어서 필요한 다음 단계를 판단해주세요.",
                    ))
                logger.info(
                    "passive_ping_pong",
                    from_agent=agent.name,
                    to_agents=[d.name for d in delegated],
                    task_id=str(task_id),
                )
    except Exception as e:
        logger.error("passive_agent_failed", agent_id=str(agent_id), task_id=str(task_id), error=str(e))
        await _recover_failed_passive_task(
            project_id=project_id,
            agent_id=agent_id,
            task_id=task_id,
            agent=agent,
            error=e,
            redis=redis,
        )
    finally:
        try:
            await _publish_event(redis, project_id, "agent_processing", {
                "agent_id": str(agent_id),
                "agent_name": agent.name if agent else "",
                "status": "completed",
                "mode": "passive",
            })
        except Exception:
            pass


# Max times a single task can be auto-recovered from a passive-mode error
# before we give up and mark it blocked. Prevents a permanently broken
# task from consuming Ollama capacity forever.
PASSIVE_RECOVERY_MAX_RETRIES = 3


async def _recover_failed_passive_task(
    *,
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    task_id: uuid.UUID,
    agent: "models.Agent | None",
    error: Exception,
    redis: Any,
) -> None:
    """Self-heal after a passive agent crashes (LLM timeout, connection
    error, transient tool failure, …).

    Strategy:
      - First N failures: flip the task back to `pending`, leave the
        assignee in place, let the GlobalDispatcher pick it up again.
      - After N: mark the task blocked so a human (or the CEO via
        dispatcher escalation) can re-plan.

    Recovery attempts are counted from the task_history rows we write here,
    so the counter survives backend restarts.
    """
    err_msg = str(error)[:300]
    err_lower = err_msg.lower()
    # Distinguish classes so the chat message + log reflect what happened.
    is_timeout = "timeout" in err_lower or "timed out" in err_lower
    is_connection = any(k in err_lower for k in ("connect", "refused", "reset", "eof", "unreachable"))
    is_rate_limit = any(k in err_lower for k in ("rate_limit", "429", "quota"))
    if is_timeout:
        err_class = "timeout"
    elif is_connection:
        err_class = "connection"
    elif is_rate_limit:
        err_class = "rate_limit"
    else:
        err_class = "unknown"

    from backend.src.models import Task, TaskHistory, TaskStatus
    from sqlalchemy import func as _sa_func

    try:
        async with async_session() as db:
            # Count prior auto-recovery attempts for this task so we can cap them.
            retry_count_row = await db.execute(
                select(_sa_func.count(TaskHistory.id)).where(
                    TaskHistory.task_id == task_id,
                    TaskHistory.actor == "auto-recovery",
                    TaskHistory.reason.like("passive error:%"),
                )
            )
            retries = int(retry_count_row.scalar_one() or 0)

            task = await db.get(Task, task_id)
            if task is None:
                logger.warning("recovery_task_missing", task_id=str(task_id))
                return

            # Friendly chat message so the user sees we're self-healing.
            # Keep it human — never surface the raw Python/LLM error text.
            _class_kr = {
                "timeout": "응답 지연",
                "connection": "연결 오류",
                "rate_limit": "요청 한도 초과",
                "unknown": "일시적 오류",
            }.get(err_class, "일시적 오류")
            if retries < PASSIVE_RECOVERY_MAX_RETRIES:
                chat = (
                    f"⚠️ {_class_kr}로 잠시 멈춰서 자동으로 다시 시도할게요 "
                    f"(재시도 {retries + 1}/{PASSIVE_RECOVERY_MAX_RETRIES})."
                )
            else:
                chat = (
                    f"⚠️ 여러 번 시도했는데도 {_class_kr}가 계속되어 이 작업은 잠시 멈춰둡니다. "
                    f"팀에서 확인이 필요합니다."
                )
            await _store_and_publish(
                db, redis, project_id,
                role="assistant", content=chat,
                agent=agent, task_id=task_id,
            )

            if retries < PASSIVE_RECOVERY_MAX_RETRIES:
                # Flip running → pending so the dispatcher requeues the SAME task.
                prev_status = task.status
                task.status = TaskStatus.pending
                task.started_at = None
                # assigned_agent_id stays the same so the same teammate retries.
                db.add(TaskHistory(
                    task_id=task_id,
                    from_status=prev_status,
                    to_status=TaskStatus.pending,
                    actor="auto-recovery",
                    reason=f"passive error: {err_class} — requeue (retry {retries + 1})",
                ))
                await db.commit()
                logger.info(
                    "passive_recovered_to_pending",
                    task_id=str(task_id), agent_id=str(agent_id),
                    error_class=err_class, retry=retries + 1,
                )
            else:
                # Give up — block for human attention.
                prev_status = task.status
                task.status = TaskStatus.blocked
                db.add(TaskHistory(
                    task_id=task_id,
                    from_status=prev_status,
                    to_status=TaskStatus.blocked,
                    actor="auto-recovery",
                    reason=f"passive error: {err_class} — giving up after {PASSIVE_RECOVERY_MAX_RETRIES} retries",
                ))
                await db.commit()
                logger.warning(
                    "passive_recovery_exhausted",
                    task_id=str(task_id), agent_id=str(agent_id),
                    error_class=err_class, retries=retries,
                )
    except Exception as recover_err:  # noqa: BLE001
        # Recovery itself can fail (DB issue, concurrent write, …). Log and
        # let the stuck-task watchdog pick up the slack.
        logger.error(
            "passive_recovery_failed",
            task_id=str(task_id), error=str(recover_err),
        )


# ── Endpoints ───────────────────────────────────────────────────────


def _msg_to_out(msg: models.ConversationMessage) -> ChatMessageOut:
    return ChatMessageOut(
        id=msg.id, role=msg.role, content=msg.content,
        agent_id=msg.agent_id, agent_name=msg.agent_name,
        task_id=msg.task_id,
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
        routed = await _route_via_worker(body.message, all_agents, history, db, redis, tenant_id=tenant_id)
        mentioned = [routed] if routed else [_find_org_root(all_agents)]
    mentioned = [a for a in mentioned if a is not None]

    # Persist user message + publish to SSE
    await _store_and_publish(db, redis, project_id, role="user", content=body.message)

    # Enqueue all agents to per-agent FIFO queues.
    from backend.src.core.agent_queue import AgentRequest, get_agent_queue_manager
    mgr = get_agent_queue_manager()
    for agent in mentioned:
        await mgr.enqueue(AgentRequest(
            mode="active",
            project_id=project_id,
            agent_id=agent.id,
            tenant_id=tenant_id,
            redis=redis,
            message=body.message,
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
