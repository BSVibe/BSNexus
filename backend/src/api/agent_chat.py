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
from backend.src.core.task_markers import strip_action_markers
from backend.src.core.auth import Permission, require_permission
from backend.src.core.goal_alignment import GoalAlignmentService
from backend.src.core.budget import BudgetService
from backend.src.core.executor.litellm_executor import LiteLLMExecutor
from backend.src.core.llm_client import LLMConfig
from backend.src.core.tenant_context import get_tenant_id
from backend.src.core.worker_dispatch import WorkerDispatcher
from backend.src.queue.streams import RedisStreamManager
from backend.src.repositories.conversation_repository import ConversationRepository
from backend.src.storage.database import async_session, get_db
from backend.src.tools.agent_tools import get_tools_for_agent
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


_MENTION_RE = re.compile(r"@\S+")



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


def _strip_all_markers(text: str) -> str:
    text = strip_action_markers(text)
    text = SET_GOAL_RE.sub("", text).strip()
    text = STATUS_RE.sub("", text).strip()
    text = DECISION_RE.sub("", text).strip()
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
    tool_actions: list[dict[str, Any]] | None = None,
    task_id: uuid.UUID | None = None,
) -> models.ConversationMessage:
    """Persist the agent's response and publish it via SSE.

    Tool side-effects (task creation, file writes, etc.) are already
    executed during the agentic loop. This function only strips leftover
    markers and stores the cleaned text.
    """
    cleaned = _strip_all_markers(response_text)
    return await _store_and_publish(
        db, redis, project_id, role="assistant", content=cleaned,
        agent=agent, actions=tool_actions or [],
        task_id=task_id,
    )


async def _call_via_executor(
    agent: models.Agent, project: models.Project, project_id: uuid.UUID,
    history: list[models.ConversationMessage], user_message: str,
    db: AsyncSession | None, redis: Any, all_agents: list[models.Agent],
    *, tenant_id: uuid.UUID,
) -> models.ConversationMessage:
    """Call the agent via LiteLLMExecutor with tool_use support.

    The executor runs an agentic loop: LLM → tool_call → execute → repeat.
    Tools (file_write, create_task, etc.) are executed during the loop,
    so no marker post-processing is needed.
    """
    # Phase A: Build context (short-lived DB session).
    async with async_session() as setup_db:
        llm_config = await _resolve_llm_config(agent, setup_db)
        _, messages = await _build_chat_context(
            agent, project, project_id, history, user_message, setup_db, all_agents, tenant_id=tenant_id,
        )
    # setup_db CLOSED — no DB held during executor run.

    # Phase B: Build tool context and handler.
    tools = get_tools_for_agent(agent.capabilities)
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

        return await _process_response_text(
            result.content, project, project_id, agent, result_db, redis,
            tenant_id=tenant_id, tool_actions=tool_actions,
            task_id=primary_task_id,
        )


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

    Checks tool calls for claim_task or complete_task (highest signal),
    then create_task as fallback.
    """
    task_tools = ("claim_task", "complete_task", "create_task")
    for tool_name in task_tools:
        for tc in (result.tool_calls_made or []):
            if tc.name == tool_name and tc.input.get("task_id"):
                try:
                    return uuid.UUID(tc.input["task_id"])
                except ValueError:
                    continue
    # create_task doesn't have task_id in input — check tool results
    for tr in (result.tool_results or []):
        if tr.is_error:
            continue
        try:
            data = __import__("json").loads(tr.content)
            if data.get("task_id"):
                return uuid.UUID(data["task_id"])
        except (ValueError, KeyError, TypeError):
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
                return await _process_response_text(
                    payload.get("output", ""), project, project_id, agent, fresh_db, redis,
                    tenant_id=tenant_id, tool_actions=tool_actions,
                )
        await asyncio.sleep(0.5)
        waited += 0.5

    raise HTTPException(status_code=504, detail=f"Worker timed out ({int(WORKER_RESULT_TIMEOUT)}s).")


async def _call_agent(
    agent: models.Agent, project: models.Project, project_id: uuid.UUID,
    history: list[models.ConversationMessage], user_message: str,
    db: AsyncSession | None, redis: Any, all_agents: list[models.Agent],
    *, tenant_id: uuid.UUID,
) -> models.ConversationMessage:
    """Route to executor; fall back to worker if LLM is unconfigured.

    ``db`` may be None — internal functions open their own short-lived
    sessions so no connection is held during the long worker poll.
    """
    if agent.executor_type == "worker":
        return await _call_via_worker(
            agent, project, project_id, history, user_message, db, redis, all_agents,
            tenant_id=tenant_id,
        )
    try:
        return await _call_via_executor(
            agent, project, project_id, history, user_message, db, redis, all_agents,
            tenant_id=tenant_id,
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

        # Agent status is now derived from task state (running/blocked).
        # No separate busy tracking needed — state machine publishes
        # task_transition SSE events that update the frontend.

        history = await ConversationRepository(setup_db).list_by_project(project_id, limit=MAX_HISTORY)
    # setup_db is now CLOSED — connection returned to pool.

    # Phase 2: call agent (may block for minutes on worker polling).
    # Each internal function opens its own short-lived session so no
    # connection is held during the long worker poll.
    try:
        msg = await _call_agent(
            agent, project, project_id, history, user_message, None, redis, all_agents,
            tenant_id=tenant_id,
        )

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
                    role="assistant", content="죄송합니다, 요청을 처리하는 중 문제가 발생했습니다. 잠시 후 다시 시도해 주세요.",
                    agent=error_agent,
                )
        except Exception:  # noqa: BLE001
            pass
    finally:
        # Agent status is derived from task state — no explicit cleanup needed.
        # When agent completes a task (via complete_task tool), the state machine
        # publishes task_transition SSE which updates the frontend dot color.
        pass


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
        routed = await _route_via_worker(body.message, all_agents, history, db, redis)
        mentioned = [routed] if routed else [_find_org_root(all_agents)]
    mentioned = [a for a in mentioned if a is not None]

    # Persist user message + publish to SSE
    await _store_and_publish(db, redis, project_id, role="user", content=body.message)

    # Dispatch all agents in parallel as background tasks.
    tasks = _project_tasks.setdefault(project_id, set())
    for agent in mentioned:
        t = asyncio.create_task(_process_agent_in_background(
            project_id, agent.id, body.message, redis, tenant_id,
        ))
        tasks.add(t)
        t.add_done_callback(lambda t, pid=project_id: _project_tasks.get(pid, set()).discard(t))

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
