"""Workers API — install-token lifecycle + worker binary endpoints.

Two audiences for this router:

1. Settings UI (Bearer JWT, tenant-scoped): mint/rotate/revoke the
   install token, list registered workers, delete a worker.
2. Worker binary (header-token, no user session): register with the
   install token, heartbeat, poll its run queue, report results.

The binary-facing endpoints authenticate via custom headers
(``X-Install-Token`` for register, ``X-Worker-Token`` for everything
else) rather than the Supabase JWT — workers aren't users.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import secrets
import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.auth import get_current_user
from backend.src.core.tenant_context import get_tenant_id
from backend.src.models import Tenant, Worker
from backend.src.schemas.worker import (
    InstallTokenCreated,
    InstallTokenStatus,
    WorkerRegisterRequest,
    WorkerRegisterResponse,
    WorkerResponse,
    WorkerResultRequest,
)
from backend.src.storage.database import get_db

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/workers", tags=["workers"])


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _get_tenant(
    db: AsyncSession, tenant_id: uuid.UUID
) -> Tenant:
    stmt = select(Tenant).where(Tenant.id == tenant_id)
    tenant = (await db.execute(stmt)).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
    return tenant


# ─── Install token ──────────────────────────────────────────────


@router.get("/install-token", response_model=InstallTokenStatus)
async def get_install_token_status(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
) -> InstallTokenStatus:
    tenant = await _get_tenant(db, tenant_id)
    return InstallTokenStatus(has_token=bool(tenant.worker_install_token_hash))


@router.post(
    "/install-token",
    response_model=InstallTokenCreated,
    status_code=status.HTTP_201_CREATED,
)
async def generate_install_token(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
) -> InstallTokenCreated:
    """Mint a fresh install token. Any previous token is replaced.

    The raw token is returned once in the response body — we only store
    its SHA-256 hash. Callers must copy it immediately.
    """
    tenant = await _get_tenant(db, tenant_id)
    raw = secrets.token_urlsafe(32)
    tenant.worker_install_token_hash = _hash_token(raw)
    await db.commit()
    logger.info("worker_install_token_rotated", tenant_id=str(tenant_id))
    return InstallTokenCreated(has_token=True, token=raw)


@router.delete("/install-token", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_install_token(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
) -> None:
    tenant = await _get_tenant(db, tenant_id)
    tenant.worker_install_token_hash = None
    await db.commit()
    logger.info("worker_install_token_revoked", tenant_id=str(tenant_id))


# ─── Workers ────────────────────────────────────────────────────


@router.get("", response_model=list[WorkerResponse])
async def list_workers(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
) -> list[Worker]:
    stmt = (
        select(Worker)
        .where(Worker.tenant_id == tenant_id)
        .order_by(Worker.created_at.asc())
    )
    return list((await db.execute(stmt)).scalars())


@router.delete("/{worker_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_worker(
    worker_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
) -> None:
    stmt = select(Worker).where(
        Worker.id == worker_id, Worker.tenant_id == tenant_id
    )
    worker = (await db.execute(stmt)).scalar_one_or_none()
    if worker is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Worker not found")
    await db.delete(worker)
    await db.commit()


# ─── Worker binary endpoints ────────────────────────────────────
# These authenticate via custom headers (not the Supabase JWT). No
# tenant-context middleware — the tenant is derived from the token.


async def _auth_by_install_token(
    x_install_token: str | None, db: AsyncSession
) -> Tenant:
    if not x_install_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing X-Install-Token")
    hashed = _hash_token(x_install_token)
    stmt = select(Tenant).where(Tenant.worker_install_token_hash == hashed)
    tenant = (await db.execute(stmt)).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid install token")
    return tenant


async def _auth_by_worker_token(
    x_worker_token: str | None, db: AsyncSession
) -> Worker:
    if not x_worker_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing X-Worker-Token")
    hashed = _hash_token(x_worker_token)
    stmt = select(Worker).where(
        Worker.token_hash == hashed, Worker.is_active.is_(True)
    )
    worker = (await db.execute(stmt)).scalar_one_or_none()
    if worker is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid worker token")
    return worker


@router.post(
    "/register",
    response_model=WorkerRegisterResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_worker(
    payload: WorkerRegisterRequest,
    x_install_token: str | None = Header(default=None, alias="X-Install-Token"),
    db: AsyncSession = Depends(get_db),
) -> WorkerRegisterResponse:
    """Trade the install token for a long-lived worker token.

    Called once by ``bsnexus-worker register``. The raw worker token is
    only ever returned here — server stores only its SHA-256 hash.
    """
    tenant = await _auth_by_install_token(x_install_token, db)
    raw_token = secrets.token_urlsafe(32)
    worker = Worker(
        tenant_id=tenant.id,
        name=payload.name,
        labels=payload.labels,
        capabilities=payload.capabilities,
        status="offline",
        last_heartbeat=None,
        token_hash=_hash_token(raw_token),
        is_active=True,
    )
    db.add(worker)
    await db.commit()
    await db.refresh(worker)
    logger.info(
        "worker_registered",
        tenant_id=str(tenant.id),
        worker_id=str(worker.id),
        capabilities=payload.capabilities,
    )
    return WorkerRegisterResponse(id=worker.id, token=raw_token)


@router.post("/heartbeat", status_code=status.HTTP_204_NO_CONTENT)
async def worker_heartbeat(
    x_worker_token: str | None = Header(default=None, alias="X-Worker-Token"),
    db: AsyncSession = Depends(get_db),
) -> None:
    worker = await _auth_by_worker_token(x_worker_token, db)
    worker.last_heartbeat = _dt.datetime.now(_dt.timezone.utc)
    if worker.status == "offline":
        worker.status = "online"
    await db.commit()


@router.post("/poll")
async def worker_poll(
    request: Request,
    count: int = 5,
    x_worker_token: str | None = Header(default=None, alias="X-Worker-Token"),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Pull queued runs off the worker's dedicated Redis stream.

    Returns an array of ``task`` dicts in the shape the worker binary
    expects: ``{task_id, project_id, action, title, prompt, ...}``.
    Empty array when nothing is queued — the worker sleeps and retries.
    """
    worker = await _auth_by_worker_token(x_worker_token, db)
    stream_manager = getattr(request.app.state, "stream_manager", None)
    if stream_manager is None:
        return []

    stream_name = f"runs:worker:{worker.id}"
    group_name = f"worker-{worker.id}"

    # Ensure the consumer group exists. Idempotent — BUSYGROUP means it
    # was already created.
    try:
        await stream_manager.redis.xgroup_create(
            stream_name, group_name, id="0", mkstream=True
        )
    except Exception as exc:  # noqa: BLE001
        if "BUSYGROUP" not in str(exc):
            logger.warning(
                "worker_poll_xgroup_create_failed",
                worker_id=str(worker.id),
                error=str(exc),
            )

    try:
        messages = await stream_manager.consume(
            stream_name,
            group_name,
            consumer=f"worker-{worker.id}",
            count=max(1, min(count, 10)),
            block=100,  # 100ms — short block so the HTTP request returns quickly
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("worker_poll_failed", worker_id=str(worker.id), error=str(exc))
        return []

    tasks: list[dict[str, Any]] = []
    for msg in messages:
        tools_allowed = msg.get("tools_allowed")
        if isinstance(tools_allowed, str):
            try:
                tools_allowed = json.loads(tools_allowed)
            except (json.JSONDecodeError, TypeError):
                tools_allowed = []
        tasks.append(
            {
                "task_id": msg.get("run_id"),
                "project_id": msg.get("project_id", ""),
                "action": msg.get("action", "execute"),
                "title": msg.get("intent_summary", ""),
                "prompt": msg.get("system_prompt", ""),
                "system_prompt": msg.get("system_prompt", ""),
                "tools_allowed": tools_allowed or [],
                "workspace_dir": msg.get("workspace_dir"),
                "_message_id": msg.get("_message_id"),
            }
        )
    return tasks


@router.post("/result", status_code=status.HTTP_204_NO_CONTENT)
async def worker_result(
    request: Request,
    payload: WorkerResultRequest,
    x_worker_token: str | None = Header(default=None, alias="X-Worker-Token"),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Worker reports the result of one run.

    Persisted onto the ``runs:results`` Redis stream so the background
    result-consumer can finalize the run (via
    ``RunOrchestrator.on_run_completed``).
    """
    worker = await _auth_by_worker_token(x_worker_token, db)
    stream_manager = getattr(request.app.state, "stream_manager", None)
    if stream_manager is None:
        logger.warning(
            "worker_result_no_stream_manager",
            worker_id=str(worker.id),
            run_id=str(payload.task_id),
        )
        return

    data: dict[str, Any] = {
        "run_id": str(payload.task_id),
        "worker_id": str(worker.id),
        "success": "true" if payload.success else "false",
        "reported_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }
    if payload.output_data:
        data["output_data"] = payload.output_data
    if payload.error_message:
        data["error_message"] = payload.error_message

    await stream_manager.publish("runs:results", data)
    logger.info(
        "worker_result_received",
        worker_id=str(worker.id),
        run_id=str(payload.task_id),
        success=payload.success,
    )
