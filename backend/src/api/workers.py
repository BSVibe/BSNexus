"""Worker API — remote runner registration, heartbeat, task polling, and management."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.tenant_context import DEFAULT_TENANT_ID
from backend.src.core.worker_dispatch import WorkerDispatcher
from backend.src.models.worker import Worker
from backend.src.storage.database import get_db

router = APIRouter(prefix="/api/v1/workers", tags=["workers"])


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


@router.post("/register", response_model=WorkerRegisterResponse, status_code=201)
async def register_worker(body: WorkerRegisterRequest, db: AsyncSession = Depends(get_db)) -> WorkerRegisterResponse:
    """Register a new remote worker. Returns a one-time token."""
    token = secrets.token_urlsafe(32)
    worker = Worker(
        tenant_id=DEFAULT_TENANT_ID,
        name=body.name,
        labels=body.labels,
        capabilities=body.capabilities,
        token_hash=_hash_token(token),
        status="online",
        last_heartbeat=datetime.now(timezone.utc),
    )
    db.add(worker)
    await db.flush()
    await db.commit()
    await db.refresh(worker)
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
    task_id: str
    project_id: str
    title: str
    action: str
    prompt: str | None = None
    dispatched_at: str | None = None


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
        tasks.append(WorkerTaskMessage(
            task_id=msg.get("task_id", ""),
            project_id=msg.get("project_id", ""),
            title=msg.get("title", ""),
            action=msg.get("action", "execute"),
            prompt=msg.get("prompt"),
            dispatched_at=msg.get("dispatched_at"),
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
    """Submit task execution result from a worker."""
    token_hash = _hash_token(x_worker_token)
    result = await db.execute(select(Worker).where(Worker.token_hash == token_hash, Worker.is_active.is_(True)))
    worker = result.scalar_one_or_none()
    if not worker:
        raise HTTPException(status_code=401, detail="Invalid worker token")

    stream_manager = getattr(request.app.state, "stream_manager", None)
    if stream_manager is None:
        return {"status": "accepted", "note": "no stream manager configured"}

    dispatcher = WorkerDispatcher(stream_manager)
    await dispatcher.report_result(
        worker_id=worker.id,
        task_id=body.task_id,
        success=body.success,
        output_data=body.output_data,
        error_message=body.error_message,
    )
    return {"status": "accepted"}


@router.get("", response_model=list[WorkerResponse])
async def list_workers(db: AsyncSession = Depends(get_db)) -> list[WorkerResponse]:
    result = await db.execute(
        select(Worker).where(Worker.tenant_id == DEFAULT_TENANT_ID, Worker.is_active.is_(True)).order_by(Worker.created_at)
    )
    workers = result.scalars().all()
    return [
        WorkerResponse(
            id=w.id,
            name=w.name,
            labels=w.labels,
            status=w.status,
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
    await db.commit()
