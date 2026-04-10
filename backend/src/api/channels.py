"""ProjectChannel CRUD — link a project to an external chat channel.

The fan-out background task ``ChannelFanout`` consumes whatever rows
this endpoint creates and posts new chat messages out to Slack /
Discord / Teams etc.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from bsvibe_auth import BSVibeUser
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src import models
from backend.src.core.auth import Permission, require_permission
from backend.src.storage.database import get_db

router = APIRouter(prefix="/api/v1/projects/{project_id}/channels", tags=["channels"])


class ChannelCreate(BaseModel):
    kind: str
    external_channel_id: str
    display_name: str | None = None
    credentials: dict[str, Any] | None = None


class ChannelUpdate(BaseModel):
    display_name: str | None = None
    credentials: dict[str, Any] | None = None
    is_active: bool | None = None


class ChannelResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    kind: str
    external_channel_id: str
    display_name: str | None = None
    is_active: bool


def _to_response(row: models.ProjectChannel) -> ChannelResponse:
    return ChannelResponse(
        id=row.id,
        project_id=row.project_id,
        kind=row.kind,
        external_channel_id=row.external_channel_id,
        display_name=row.display_name,
        is_active=row.is_active,
    )


def _serialize_credentials(creds: dict[str, Any] | None) -> str | None:
    if creds is None:
        return None
    # Real implementations should encrypt this blob with the project
    # encryption key. JSON is fine for the dev path; the column is
    # already named ``credentials_encrypted`` so the upgrade is a
    # transparent encrypt/decrypt swap later.
    return json.dumps(creds)


@router.get("", response_model=list[ChannelResponse])
async def list_channels(
    project_id: uuid.UUID,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_read)),
    db: AsyncSession = Depends(get_db),
) -> list[ChannelResponse]:
    result = await db.execute(
        select(models.ProjectChannel)
        .where(models.ProjectChannel.project_id == project_id)
        .order_by(models.ProjectChannel.created_at.asc())
    )
    return [_to_response(c) for c in result.scalars().all()]


@router.post("", response_model=ChannelResponse, status_code=201)
async def create_channel(
    project_id: uuid.UUID,
    body: ChannelCreate,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_update)),
    db: AsyncSession = Depends(get_db),
) -> ChannelResponse:
    # Verify project exists.
    project_exists = await db.execute(
        select(models.Project.id).where(models.Project.id == project_id)
    )
    if project_exists.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Project not found")

    channel = models.ProjectChannel(
        project_id=project_id,
        kind=body.kind,
        external_channel_id=body.external_channel_id,
        display_name=body.display_name,
        credentials_encrypted=_serialize_credentials(body.credentials),
    )
    db.add(channel)
    await db.flush()
    await db.commit()
    await db.refresh(channel)
    return _to_response(channel)


@router.patch("/{channel_id}", response_model=ChannelResponse)
async def update_channel(
    project_id: uuid.UUID,
    channel_id: uuid.UUID,
    body: ChannelUpdate,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_update)),
    db: AsyncSession = Depends(get_db),
) -> ChannelResponse:
    result = await db.execute(
        select(models.ProjectChannel).where(
            models.ProjectChannel.id == channel_id,
            models.ProjectChannel.project_id == project_id,
        )
    )
    channel = result.scalar_one_or_none()
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")

    if body.display_name is not None:
        channel.display_name = body.display_name
    if body.is_active is not None:
        channel.is_active = body.is_active
    if body.credentials is not None:
        channel.credentials_encrypted = _serialize_credentials(body.credentials)

    await db.commit()
    await db.refresh(channel)
    return _to_response(channel)


@router.delete("/{channel_id}", status_code=204)
async def delete_channel(
    project_id: uuid.UUID,
    channel_id: uuid.UUID,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_update)),
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(
        select(models.ProjectChannel).where(
            models.ProjectChannel.id == channel_id,
            models.ProjectChannel.project_id == project_id,
        )
    )
    channel = result.scalar_one_or_none()
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    await db.delete(channel)
    await db.commit()
