"""Workers API — install-token lifecycle + worker list/revoke.

Workers are remote runners that register against a tenant using the
install token. Actual registration/heartbeat endpoints (used by the
worker binary) aren't in this router yet — v1 only ships the
Settings-page surface: generate/rotate/revoke the tenant's install
token and list + delete registered workers.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.auth import get_current_user
from backend.src.core.tenant_context import get_tenant_id
from backend.src.models import Tenant, Worker
from backend.src.schemas.worker import (
    InstallTokenCreated,
    InstallTokenStatus,
    WorkerResponse,
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
