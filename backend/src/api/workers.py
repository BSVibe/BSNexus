"""Worker API — remote runner registration, heartbeat, task polling, and management."""

from __future__ import annotations

import hashlib
import io
import json
import secrets
import tarfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Header, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.api.settings import resolve_install_token_tenant
from backend.src.core.tenant_context import DEFAULT_TENANT_ID, get_tenant_id
from backend.src.core.worker_dispatch import WorkerDispatcher
from backend.src.models import Agent, ExecutorConfig, Task, TaskStatus
from backend.src.models.worker import Worker
from backend.src.storage.database import get_db

# Workers with no heartbeat for this long are considered offline
_HEARTBEAT_TIMEOUT_SECONDS = 60

router = APIRouter(prefix="/api/v1/workers", tags=["workers"])

# ─── Static: install script & source bundle ─────────────────────
_WORKER_DIR = Path(__file__).resolve().parents[3] / "worker"
_INSTALL_SCRIPT = _WORKER_DIR / "install.sh"


@router.get("/install.sh", response_class=PlainTextResponse, include_in_schema=False)
async def get_install_script(request: Request) -> PlainTextResponse:
    """Serve the worker install script with server URL auto-injected."""
    if not _INSTALL_SCRIPT.is_file():
        raise HTTPException(status_code=404, detail="install.sh not found")
    # Inject the server origin — prefer forwarded headers (Vite proxy, nginx, etc.)
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or "localhost:8000"
    )
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    origin = f"{scheme}://{host}"
    content = _INSTALL_SCRIPT.read_text().replace(
        'SERVER_URL="${BSNEXUS_SERVER_URL:-}"',
        f'SERVER_URL="${{BSNEXUS_SERVER_URL:-{origin}}}"',
    )
    return PlainTextResponse(content, media_type="text/plain")


_cached_tarball: bytes | None = None


def _build_worker_tarball() -> bytes:
    """Build worker source tarball. Cached after first call."""
    global _cached_tarball  # noqa: PLW0603
    if _cached_tarball is not None:
        return _cached_tarball
    src_dir = _WORKER_DIR / "worker"
    pyproject = _WORKER_DIR / "pyproject.toml"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(str(pyproject), arcname="pyproject.toml")
        for f in src_dir.rglob("*.py"):
            tar.add(str(f), arcname=f"worker/{f.relative_to(src_dir)}")
    _cached_tarball = buf.getvalue()
    return _cached_tarball


@router.get("/source.tar.gz", include_in_schema=False)
async def get_worker_source() -> StreamingResponse:
    """Serve the worker source as a tarball for remote installation."""
    src_dir = _WORKER_DIR / "worker"
    pyproject = _WORKER_DIR / "pyproject.toml"
    if not src_dir.is_dir() or not pyproject.is_file():
        raise HTTPException(status_code=404, detail="Worker source not found")

    data = _build_worker_tarball()
    return StreamingResponse(io.BytesIO(data), media_type="application/gzip", headers={
        "Content-Disposition": "attachment; filename=bsnexus-worker.tar.gz",
    })


class WorkerRegisterRequest(BaseModel):
    name: str
    labels: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=lambda: ["claude_code"])


class WorkerRegisterResponse(BaseModel):
    id: uuid.UUID
    token: str  # Only returned once at registration


class WorkerResponse(BaseModel):
    id: uuid.UUID
    name: str
    labels: list[str]
    status: str
    last_heartbeat: datetime | None
    capabilities: list[str]
    created_at: datetime


class WorkerHeartbeatResponse(BaseModel):
    status: str
    next_task_id: uuid.UUID | None = None


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _worker_description(capabilities: list[str]) -> str:
    return f"Self-hosted worker ({', '.join(capabilities)})"


def _make_worker_executor_config(
    name: str, worker_id: uuid.UUID, capabilities: list[str], tenant_id: uuid.UUID
) -> ExecutorConfig:
    return ExecutorConfig(
        tenant_id=tenant_id,
        name=f"Worker: {name}",
        executor_type="worker",
        config={"worker_id": str(worker_id)},
        description=_worker_description(capabilities),
    )


@router.post("/register", response_model=WorkerRegisterResponse, status_code=201)
async def register_worker(
    body: WorkerRegisterRequest,
    x_install_token: str = Header("", alias="X-Install-Token"),
    db: AsyncSession = Depends(get_db),
) -> WorkerRegisterResponse:
    """Register or re-register a worker. Same name (within tenant) = update existing.

    The install token determines the worker's tenant. Tokens are minted
    via Settings → Install Token by an admin of the target tenant.
    """
    install_tenant_id: uuid.UUID | None = None
    if x_install_token:
        install_tenant_id = await resolve_install_token_tenant(x_install_token, db)
        if install_tenant_id is None:
            raise HTTPException(
                status_code=401,
                detail="Invalid install token. Generate one in Settings → Install Token.",
            )
    else:
        # Open-mode fallback: no token at all means single-tenant dev. We
        # only allow this when the default tenant has not minted its own
        # token yet (otherwise the admin clearly wants tokens enforced).
        from backend.src.models import Tenant

        result = await db.execute(select(Tenant).where(Tenant.id == DEFAULT_TENANT_ID))
        default_tenant = result.scalar_one_or_none()
        if default_tenant is None or default_tenant.worker_install_token_hash is not None:
            raise HTTPException(
                status_code=401,
                detail="Install token required. Generate one in Settings → Install Token.",
            )
        install_tenant_id = DEFAULT_TENANT_ID

    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)

    # Check for existing workers with same name in the same tenant
    result = await db.execute(
        select(Worker).where(
            Worker.tenant_id == install_tenant_id,
            Worker.name == body.name,
        ).order_by(Worker.last_heartbeat.desc().nulls_last())
    )
    existing = list(result.scalars().all())
    worker = existing[0] if existing else None

    if worker:
        # Deactivate duplicates (keep only the first)
        for dup in existing[1:]:
            dup.is_active = False
            dup.status = "offline"
            # Remove linked ExecutorConfig for duplicate
            dup_linked = await db.execute(
                select(ExecutorConfig).where(
                    ExecutorConfig.executor_type == "worker",
                    ExecutorConfig.config["worker_id"].as_string() == str(dup.id),
                )
            )
            for ec in dup_linked.scalars().all():
                await db.delete(ec)

        # Re-register: update token, capabilities, reactivate
        worker.token_hash = _hash_token(token)
        worker.capabilities = body.capabilities
        worker.labels = body.labels
        worker.status = "online"
        worker.last_heartbeat = now
        worker.is_active = True
        await db.flush()

        # Update linked ExecutorConfig
        linked = await db.execute(
            select(ExecutorConfig).where(
                ExecutorConfig.executor_type == "worker",
                ExecutorConfig.config["worker_id"].as_string() == str(worker.id),
            )
        )
        exec_config = linked.scalar_one_or_none()
        if exec_config:
            exec_config.description = _worker_description(body.capabilities)
        else:
            db.add(
                _make_worker_executor_config(
                    body.name, worker.id, body.capabilities, install_tenant_id
                )
            )
    else:
        # New registration
        worker = Worker(
            tenant_id=install_tenant_id,
            name=body.name,
            labels=body.labels,
            capabilities=body.capabilities,
            token_hash=_hash_token(token),
            status="online",
            last_heartbeat=now,
        )
        db.add(worker)
        await db.flush()
        await db.refresh(worker)

        db.add(
            _make_worker_executor_config(
                body.name, worker.id, body.capabilities, install_tenant_id
            )
        )

    await db.commit()
    return WorkerRegisterResponse(id=worker.id, token=token)


@router.post("/heartbeat", response_model=WorkerHeartbeatResponse)
async def worker_heartbeat(
    x_worker_token: str = Header(..., alias="X-Worker-Token"),
    db: AsyncSession = Depends(get_db),
) -> WorkerHeartbeatResponse:
    """Worker heartbeat — updates status and checks for pending tasks."""
    token_hash = _hash_token(x_worker_token)
    result = await db.execute(select(Worker).where(Worker.token_hash == token_hash, Worker.is_active.is_(True)))
    worker = result.scalar_one_or_none()
    if not worker:
        raise HTTPException(status_code=401, detail="Invalid worker token")

    await db.execute(
        update(Worker)
        .where(Worker.id == worker.id)
        .values(status="online", last_heartbeat=datetime.now(timezone.utc))
    )
    await db.commit()

    return WorkerHeartbeatResponse(status="online", next_task_id=None)


class WorkerTaskMessage(BaseModel):
    task_id: str = ""
    project_id: str = ""
    title: str = ""
    action: str = "execute"
    prompt: str | None = None
    dispatched_at: str | None = None
    # Chat-specific fields
    chat_id: str = ""
    message: str = ""
    system_prompt: str = ""
    history: str = "[]"


class WorkerResultRequest(BaseModel):
    task_id: uuid.UUID
    success: bool
    output_data: dict | None = None
    error_message: str | None = None


@router.post("/poll", response_model=list[WorkerTaskMessage])
async def poll_tasks(
    request: Request,
    x_worker_token: str = Header(..., alias="X-Worker-Token"),
    count: int = 1,
    db: AsyncSession = Depends(get_db),
) -> list[WorkerTaskMessage]:
    """Poll for tasks from the worker's dedicated Redis stream."""
    token_hash = _hash_token(x_worker_token)
    result = await db.execute(select(Worker).where(Worker.token_hash == token_hash, Worker.is_active.is_(True)))
    worker = result.scalar_one_or_none()
    if not worker:
        raise HTTPException(status_code=401, detail="Invalid worker token")

    stream_manager = getattr(request.app.state, "stream_manager", None)
    if stream_manager is None:
        return []

    stream_name = f"{WorkerDispatcher.WORKER_STREAM_PREFIX}{worker.id}"
    group_name = f"worker-{worker.id}"

    # Ensure consumer group exists
    try:
        await stream_manager.redis.xgroup_create(stream_name, group_name, id="0", mkstream=True)
    except Exception:
        pass  # Group may already exist

    messages = await stream_manager.consume(
        stream=stream_name,
        group=group_name,
        consumer=f"worker-{worker.id}-0",
        count=count,
        block=1000,  # 1 second blocking poll
    )

    tasks: list[WorkerTaskMessage] = []
    for msg in messages:
        # `consume()` auto-decodes JSON fields, so `history` may be a list/dict.
        # Re-serialize to keep WorkerTaskMessage.history a string for the worker.
        history_val = msg.get("history", "[]")
        if not isinstance(history_val, str):
            history_val = json.dumps(history_val)
        tasks.append(WorkerTaskMessage(
            task_id=msg.get("task_id", ""),
            project_id=msg.get("project_id", ""),
            title=msg.get("title", ""),
            action=msg.get("action", "execute"),
            prompt=msg.get("prompt"),
            dispatched_at=msg.get("dispatched_at"),
            chat_id=msg.get("chat_id", ""),
            message=msg.get("message", ""),
            system_prompt=msg.get("system_prompt", ""),
            history=history_val,
        ))
        # Auto-ack after delivery
        msg_id = msg.get("_message_id")
        if msg_id:
            await stream_manager.acknowledge(stream_name, group_name, msg_id)

    return tasks


@router.post("/result", status_code=200)
async def submit_result(
    body: WorkerResultRequest,
    request: Request,
    x_worker_token: str = Header(..., alias="X-Worker-Token"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Submit task execution result from a worker.

    Updates task status, publishes the result to the stream manager, and posts
    a chat notification so the user sees task progress in the chat sidebar.
    """
    token_hash = _hash_token(x_worker_token)
    result = await db.execute(select(Worker).where(Worker.token_hash == token_hash, Worker.is_active.is_(True)))
    worker = result.scalar_one_or_none()
    if not worker:
        raise HTTPException(status_code=401, detail="Invalid worker token")

    # Update task status
    task_result = await db.execute(select(Task).where(Task.id == body.task_id))
    task = task_result.scalar_one_or_none()
    if task:
        task.status = TaskStatus.running if body.success else TaskStatus.pending
        if body.output_data:
            task.output_data = body.output_data
        if body.error_message:
            task.error_message = body.error_message

        # Emit a milestone TaskActivity row so the Plan detail panel sees the result.
        from backend.src.models import ActivityLevel as _ActivityLevel
        from backend.src.models import TaskActivity as _TaskActivity
        if body.success:
            summary = "Worker reported success"
        else:
            summary = body.error_message or "Worker reported failure"
        db.add(
            _TaskActivity(
                task_id=task.id,
                project_id=task.project_id,
                agent_id=task.creator_agent_id,
                level=_ActivityLevel.milestone,
                event_type="worker_result",
                summary=summary[:2000],
                detail={
                    "success": body.success,
                    "worker_id": str(worker.id),
                    **({"output_data": body.output_data} if body.output_data else {}),
                    **({"error_message": body.error_message} if body.error_message else {}),
                },
            )
        )

        # If the worker reported individual tool calls, store them as ``tool``
        # activity rows so the detail panel can reveal them on demand.
        tool_log = (body.output_data or {}).get("tool_log") if body.output_data else None
        if isinstance(tool_log, list):
            for entry in tool_log:
                if not isinstance(entry, dict):
                    continue
                event_type = str(entry.get("type") or entry.get("tool") or "tool_call")[:64]
                summary = str(entry.get("summary") or entry.get("description") or event_type)[:2000]
                db.add(
                    _TaskActivity(
                        task_id=task.id,
                        project_id=task.project_id,
                        agent_id=task.creator_agent_id,
                        level=_ActivityLevel.tool,
                        event_type=event_type,
                        summary=summary,
                        detail=entry,
                    )
                )

        await db.commit()

    stream_manager = getattr(request.app.state, "stream_manager", None)
    if stream_manager is not None:
        dispatcher = WorkerDispatcher(stream_manager)
        await dispatcher.report_result(
            worker_id=worker.id,
            task_id=body.task_id,
            success=body.success,
            output_data=body.output_data,
            error_message=body.error_message,
        )

    # Post a chat notification so the user sees task completion in real time
    if task:
        from backend.src.api.agent_chat import _publish_event
        redis = getattr(request.app.state, "redis", None)
        if redis is not None:
            agent_name = None
            if task.creator_agent_id:
                agent_result = await db.execute(select(Agent).where(Agent.id == task.creator_agent_id))
                agent = agent_result.scalar_one_or_none()
                if agent:
                    agent_name = agent.name

            status_emoji = "✅" if body.success else "❌"
            output_preview = ""
            if body.success and body.output_data:
                stdout = body.output_data.get("stdout", "")
                if stdout:
                    output_preview = f"\n\n```\n{stdout[:1500]}\n```"
            elif body.error_message:
                output_preview = f"\n\n**Error:** {body.error_message[:500]}"

            content = f"{status_emoji} Task **{task.title}** {'completed' if body.success else 'failed'}{output_preview}"

            from backend.src.repositories.conversation_repository import ConversationRepository
            from datetime import datetime as _dt
            repo = ConversationRepository(db)
            msg = await repo.append(
                task.project_id,
                role="assistant",
                content=content,
                agent_id=task.creator_agent_id,
                agent_name=agent_name,
            )
            await db.commit()
            await _publish_event(redis, task.project_id, "message_created", {
                "id": str(msg.id),
                "role": msg.role,
                "content": msg.content,
                "agent_id": str(msg.agent_id) if msg.agent_id else None,
                "agent_name": msg.agent_name,
                "actions": msg.actions or [],
                "created_at": msg.created_at.isoformat() if hasattr(msg.created_at, "isoformat") else _dt.utcnow().isoformat(),
            })

    return {"status": "accepted"}


class WorkerChatResultRequest(BaseModel):
    chat_id: str
    success: bool
    output: str = ""
    error_message: str | None = None
    # Tool audit trail from agentic loop execution.
    tool_calls: list[dict] | None = None
    tool_results: list[dict] | None = None
    usage: dict | None = None


@router.post("/chat-result", status_code=200)
async def submit_chat_result(
    body: WorkerChatResultRequest,
    request: Request,
    x_worker_token: str = Header(..., alias="X-Worker-Token"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Submit chat execution result from a worker. Stored in Redis for polling."""
    token_hash = _hash_token(x_worker_token)
    result = await db.execute(select(Worker).where(Worker.token_hash == token_hash, Worker.is_active.is_(True)))
    worker = result.scalar_one_or_none()
    if not worker:
        raise HTTPException(status_code=401, detail="Invalid worker token")

    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        raise HTTPException(status_code=500, detail="Redis not available")

    import json
    result_key = f"chat:result:{body.chat_id}"
    await redis.set(result_key, json.dumps({
        "success": body.success,
        "output": body.output,
        "error_message": body.error_message,
        "worker_id": str(worker.id),
        "tool_calls": body.tool_calls or [],
        "tool_results": body.tool_results or [],
        "usage": body.usage or {},
    }), ex=3600)  # TTL 1 hour — must outlive WORKER_RESULT_TIMEOUT (30min)

    return {"status": "accepted"}


def _compute_status(worker: Worker) -> str:
    """Compute worker status based on last heartbeat."""
    if not worker.last_heartbeat:
        return "offline"
    hb = worker.last_heartbeat
    # SQLite returns naive datetimes — treat as UTC
    if hb.tzinfo is None:
        hb = hb.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - hb).total_seconds()
    return "online" if age < _HEARTBEAT_TIMEOUT_SECONDS else "offline"


@router.get("", response_model=list[WorkerResponse])
async def list_workers(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> list[WorkerResponse]:
    result = await db.execute(
        select(Worker).where(Worker.tenant_id == tenant_id, Worker.is_active.is_(True)).order_by(Worker.created_at)
    )
    workers = result.scalars().all()

    # Compute status once per worker; update stale entries in DB
    statuses: dict[uuid.UUID, str] = {}
    for w in workers:
        statuses[w.id] = _compute_status(w)
        if w.status != statuses[w.id]:
            await db.execute(update(Worker).where(Worker.id == w.id).values(status=statuses[w.id]))
    await db.commit()

    return [
        WorkerResponse(
            id=w.id,
            name=w.name,
            labels=w.labels,
            status=statuses[w.id],
            last_heartbeat=w.last_heartbeat,
            capabilities=w.capabilities,
            created_at=w.created_at,
        )
        for w in workers
    ]


@router.delete("/{worker_id}", status_code=204)
async def deregister_worker(worker_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> None:
    result = await db.execute(select(Worker).where(Worker.id == worker_id))
    worker = result.scalar_one_or_none()
    if not worker:
        raise HTTPException(status_code=404, detail="Worker not found")
    await db.execute(update(Worker).where(Worker.id == worker_id).values(is_active=False, status="offline"))
    # Remove linked ExecutorConfig
    linked = await db.execute(
        select(ExecutorConfig).where(
            ExecutorConfig.executor_type == "worker",
            ExecutorConfig.config["worker_id"].as_string() == str(worker_id),
        )
    )
    for ec in linked.scalars().all():
        await db.delete(ec)
    await db.commit()
