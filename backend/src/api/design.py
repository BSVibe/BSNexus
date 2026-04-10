"""Builtin design tool API.

Replaces the external Stitch MCP integration with first-party endpoints
that the Designer agent can call directly. Each project owns at most one
DesignSystem (with tokens, components, patterns) and any number of
Screens whose specs reference that design system.

Phase 5 in the overhaul roadmap. The actual Designer agent workflow
(natural-language requirements -> screen spec -> code generation) lands
incrementally on top of these CRUD endpoints.
"""

from __future__ import annotations

import uuid
from typing import Any

from bsvibe_auth import BSVibeUser
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src import models
from backend.src.core.auth import Permission, require_permission
from backend.src.storage.database import get_db

router = APIRouter(prefix="/api/v1/projects/{project_id}/design", tags=["design"])


# ── Schemas ──────────────────────────────────────────────────────────


class DesignSystemPayload(BaseModel):
    name: str = "Default"
    tokens: dict[str, Any] = Field(default_factory=dict)
    components: dict[str, Any] = Field(default_factory=dict)
    patterns: dict[str, Any] = Field(default_factory=dict)
    brand_voice: str | None = None


class DesignSystemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    tokens: dict[str, Any]
    components: dict[str, Any]
    patterns: dict[str, Any]
    brand_voice: str | None = None


class ScreenCreate(BaseModel):
    name: str
    route: str | None = None
    intent: str | None = None
    spec: dict[str, Any] = Field(default_factory=dict)


class ScreenUpdate(BaseModel):
    name: str | None = None
    route: str | None = None
    intent: str | None = None
    spec: dict[str, Any] | None = None
    generated_code_path: str | None = None
    preview_image_path: str | None = None


class ScreenResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    design_system_id: uuid.UUID
    name: str
    route: str | None = None
    intent: str | None = None
    spec: dict[str, Any]
    generated_code_path: str | None = None
    preview_image_path: str | None = None


# ── Helpers ──────────────────────────────────────────────────────────


async def _get_or_create_design_system(
    db: AsyncSession, project_id: uuid.UUID
) -> models.DesignSystem:
    """Return the project's design system, creating an empty one on first call."""
    result = await db.execute(
        select(models.DesignSystem).where(models.DesignSystem.project_id == project_id)
    )
    design_system = result.scalar_one_or_none()
    if design_system is not None:
        return design_system

    # Verify the project exists before inserting an orphan design system.
    project_result = await db.execute(
        select(models.Project.id).where(models.Project.id == project_id)
    )
    if project_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Project not found")

    design_system = models.DesignSystem(project_id=project_id)
    db.add(design_system)
    await db.flush()
    return design_system


# ── DesignSystem endpoints ───────────────────────────────────────────


@router.get("/system", response_model=DesignSystemResponse)
async def get_design_system(
    project_id: uuid.UUID,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_read)),
    db: AsyncSession = Depends(get_db),
) -> DesignSystemResponse:
    """Return (or lazily create) the project's design system."""
    design_system = await _get_or_create_design_system(db, project_id)
    await db.commit()
    return DesignSystemResponse.model_validate(design_system)


@router.put("/system", response_model=DesignSystemResponse)
async def upsert_design_system(
    project_id: uuid.UUID,
    body: DesignSystemPayload,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_update)),
    db: AsyncSession = Depends(get_db),
) -> DesignSystemResponse:
    """Replace the project's design system contents."""
    design_system = await _get_or_create_design_system(db, project_id)
    design_system.name = body.name
    design_system.tokens = body.tokens
    design_system.components = body.components
    design_system.patterns = body.patterns
    design_system.brand_voice = body.brand_voice
    await db.commit()
    await db.refresh(design_system)
    return DesignSystemResponse.model_validate(design_system)


# ── Screen endpoints ─────────────────────────────────────────────────


@router.get("/screens", response_model=list[ScreenResponse])
async def list_screens(
    project_id: uuid.UUID,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_read)),
    db: AsyncSession = Depends(get_db),
) -> list[ScreenResponse]:
    result = await db.execute(
        select(models.Screen)
        .where(models.Screen.project_id == project_id)
        .order_by(models.Screen.created_at.asc())
    )
    return [ScreenResponse.model_validate(s) for s in result.scalars().all()]


@router.post("/screens", response_model=ScreenResponse, status_code=201)
async def create_screen(
    project_id: uuid.UUID,
    body: ScreenCreate,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_update)),
    db: AsyncSession = Depends(get_db),
) -> ScreenResponse:
    design_system = await _get_or_create_design_system(db, project_id)
    screen = models.Screen(
        project_id=project_id,
        design_system_id=design_system.id,
        name=body.name,
        route=body.route,
        intent=body.intent,
        spec=body.spec,
    )
    db.add(screen)
    await db.flush()
    await db.commit()
    await db.refresh(screen)
    return ScreenResponse.model_validate(screen)


@router.get("/screens/{screen_id}", response_model=ScreenResponse)
async def get_screen(
    project_id: uuid.UUID,
    screen_id: uuid.UUID,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_read)),
    db: AsyncSession = Depends(get_db),
) -> ScreenResponse:
    result = await db.execute(
        select(models.Screen).where(
            models.Screen.id == screen_id, models.Screen.project_id == project_id
        )
    )
    screen = result.scalar_one_or_none()
    if screen is None:
        raise HTTPException(status_code=404, detail="Screen not found")
    return ScreenResponse.model_validate(screen)


@router.patch("/screens/{screen_id}", response_model=ScreenResponse)
async def update_screen(
    project_id: uuid.UUID,
    screen_id: uuid.UUID,
    body: ScreenUpdate,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_update)),
    db: AsyncSession = Depends(get_db),
) -> ScreenResponse:
    result = await db.execute(
        select(models.Screen).where(
            models.Screen.id == screen_id, models.Screen.project_id == project_id
        )
    )
    screen = result.scalar_one_or_none()
    if screen is None:
        raise HTTPException(status_code=404, detail="Screen not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(screen, field, value)

    await db.commit()
    await db.refresh(screen)
    return ScreenResponse.model_validate(screen)


@router.delete("/screens/{screen_id}", status_code=204)
async def delete_screen(
    project_id: uuid.UUID,
    screen_id: uuid.UUID,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_update)),
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(
        select(models.Screen).where(
            models.Screen.id == screen_id, models.Screen.project_id == project_id
        )
    )
    screen = result.scalar_one_or_none()
    if screen is None:
        raise HTTPException(status_code=404, detail="Screen not found")
    await db.delete(screen)
    await db.commit()
