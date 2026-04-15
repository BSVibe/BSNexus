"""Builtin design tool API — workspace-backed .bsd files.

Replaces the Stitch MCP integration. The Designer agent works with two
file kinds inside the project workspace:

  <workspace>/design/system.bsd          — DesignSystem (tokens, components,
                                            patterns, brand_voice)
  <workspace>/design/screens/<slug>.bsd  — One Screen per file (name,
                                            route, intent, spec, optional
                                            generated_code preview)

Each .bsd file is JSON. The format is intentionally schemaless beyond a
few well-known top-level keys so the Designer agent can extend it as
the design system matures without DB migrations.

This API is a thin wrapper around WorkspaceService. Designer-typed
agents call the same workspace tools every other worker uses, but their
system prompt instructs them to read and write .bsd files instead of
production source files.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any

from bsvibe_auth import BSVibeUser
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src import models
from backend.src.core.auth import Permission, require_permission
from backend.src.core.workspace import LocalStorageBackend, WorkspaceService
from backend.src.storage.database import get_db

router = APIRouter(prefix="/api/v1/projects/{project_id}/design", tags=["design"])

DESIGN_DIR = "design"
SYSTEM_FILE = f"{DESIGN_DIR}/system.bsd"
SCREEN_DIR = f"{DESIGN_DIR}/screens"
SCREEN_EXT = ".bsd"

_workspace_service = WorkspaceService(
    LocalStorageBackend(os.environ.get("WORKSPACE_BASE_DIR", "data/workspaces"))
)


# ── Schemas ──────────────────────────────────────────────────────────


class DesignSystemPayload(BaseModel):
    name: str = "Default"
    tokens: dict[str, Any] = Field(default_factory=dict)
    components: dict[str, Any] = Field(default_factory=dict)
    patterns: dict[str, Any] = Field(default_factory=dict)
    brand_voice: str | None = None


class DesignSystemResponse(BaseModel):
    project_id: uuid.UUID
    name: str
    tokens: dict[str, Any]
    components: dict[str, Any]
    patterns: dict[str, Any]
    brand_voice: str | None = None
    path: str


class ScreenPayload(BaseModel):
    """The full body of a .bsd screen file."""

    name: str
    route: str | None = None
    intent: str | None = None
    spec: dict[str, Any] = Field(default_factory=dict)
    generated_code: str | None = None


class ScreenResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: uuid.UUID
    slug: str
    path: str
    name: str
    route: str | None = None
    intent: str | None = None
    spec: dict[str, Any]
    generated_code: str | None = None


class ScreenSummary(BaseModel):
    project_id: uuid.UUID
    slug: str
    path: str
    name: str
    route: str | None = None


# ── Helpers ──────────────────────────────────────────────────────────


_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def _slugify(value: str) -> str:
    slug = _SLUG_RE.sub("-", value.lower()).strip("-")
    return slug or "screen"


async def _ensure_project_exists(db: AsyncSession, project_id: uuid.UUID) -> models.Project:
    result = await db.execute(
        select(models.Project).where(models.Project.id == project_id)
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


async def _ensure_workspace(project: models.Project) -> uuid.UUID:
    """Make sure the workspace dir exists. Returns the project id used as workspace id."""
    if not await _workspace_service.file_exists(project.id, DESIGN_DIR):
        # The storage backend creates parent dirs on write; touching a
        # placeholder ensures the design folder exists for listings.
        await _workspace_service.write_file(
            project.id, f"{DESIGN_DIR}/.keep", b""
        )
    return project.id


async def _read_json(project_id: uuid.UUID, path: str) -> dict[str, Any] | None:
    if not await _workspace_service.file_exists(project_id, path):
        return None
    raw = await _workspace_service.read_file(project_id, path)
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=500, detail=f"Corrupt .bsd file at {path}: {e}"
        ) from e


async def _write_json(project_id: uuid.UUID, path: str, data: dict[str, Any]) -> None:
    payload = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
    await _workspace_service.write_file(project_id, path, payload)


def _screen_path(slug: str) -> str:
    return f"{SCREEN_DIR}/{slug}{SCREEN_EXT}"


# ── DesignSystem endpoints ───────────────────────────────────────────


@router.get("/system", response_model=DesignSystemResponse)
async def get_design_system(
    project_id: uuid.UUID,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_read)),
    db: AsyncSession = Depends(get_db),
) -> DesignSystemResponse:
    """Return the project's design system, lazily creating an empty one."""
    project = await _ensure_project_exists(db, project_id)
    await _ensure_workspace(project)

    data = await _read_json(project_id, SYSTEM_FILE)
    if data is None:
        data = {
            "name": "Default",
            "tokens": {},
            "components": {},
            "patterns": {},
            "brand_voice": None,
        }
        await _write_json(project_id, SYSTEM_FILE, data)

    return DesignSystemResponse(
        project_id=project_id,
        name=data.get("name", "Default"),
        tokens=data.get("tokens", {}),
        components=data.get("components", {}),
        patterns=data.get("patterns", {}),
        brand_voice=data.get("brand_voice"),
        path=SYSTEM_FILE,
    )


@router.put("/system", response_model=DesignSystemResponse)
async def upsert_design_system(
    project_id: uuid.UUID,
    body: DesignSystemPayload,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_update)),
    db: AsyncSession = Depends(get_db),
) -> DesignSystemResponse:
    project = await _ensure_project_exists(db, project_id)
    await _ensure_workspace(project)
    data = body.model_dump()
    await _write_json(project_id, SYSTEM_FILE, data)
    return DesignSystemResponse(
        project_id=project_id,
        path=SYSTEM_FILE,
        **data,
    )


# ── Screen endpoints ─────────────────────────────────────────────────


@router.get("/screens", response_model=list[ScreenSummary])
async def list_screens(
    project_id: uuid.UUID,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_read)),
    db: AsyncSession = Depends(get_db),
) -> list[ScreenSummary]:
    project = await _ensure_project_exists(db, project_id)
    await _ensure_workspace(project)

    if not await _workspace_service.file_exists(project_id, SCREEN_DIR):
        return []

    files = await _workspace_service.list_files(project_id, SCREEN_DIR, recursive=False)
    summaries: list[ScreenSummary] = []
    for f in files:
        if f.is_dir or not f.path.endswith(SCREEN_EXT):
            continue
        data = await _read_json(project_id, f.path)
        if data is None:
            continue
        slug = os.path.basename(f.path)[: -len(SCREEN_EXT)]
        summaries.append(
            ScreenSummary(
                project_id=project_id,
                slug=slug,
                path=f.path,
                name=str(data.get("name") or slug),
                route=data.get("route"),
            )
        )
    summaries.sort(key=lambda s: s.name.lower())
    return summaries


@router.post("/screens", response_model=ScreenResponse, status_code=201)
async def create_screen(
    project_id: uuid.UUID,
    body: ScreenPayload,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_update)),
    db: AsyncSession = Depends(get_db),
) -> ScreenResponse:
    project = await _ensure_project_exists(db, project_id)
    await _ensure_workspace(project)

    slug = _slugify(body.name)
    path = _screen_path(slug)

    # Avoid silently overwriting an existing screen with the same slug.
    if await _workspace_service.file_exists(project_id, path):
        # Append a numeric suffix until we find a free slot.
        counter = 2
        while True:
            candidate = f"{slug}-{counter}"
            candidate_path = _screen_path(candidate)
            if not await _workspace_service.file_exists(project_id, candidate_path):
                slug = candidate
                path = candidate_path
                break
            counter += 1

    data = body.model_dump()
    await _write_json(project_id, path, data)

    return ScreenResponse(
        project_id=project_id,
        slug=slug,
        path=path,
        **data,
    )


@router.get("/screens/{slug}", response_model=ScreenResponse)
async def get_screen(
    project_id: uuid.UUID,
    slug: str,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_read)),
    db: AsyncSession = Depends(get_db),
) -> ScreenResponse:
    await _ensure_project_exists(db, project_id)
    path = _screen_path(slug)
    data = await _read_json(project_id, path)
    if data is None:
        raise HTTPException(status_code=404, detail="Screen not found")
    return ScreenResponse(
        project_id=project_id,
        slug=slug,
        path=path,
        name=str(data.get("name") or slug),
        route=data.get("route"),
        intent=data.get("intent"),
        spec=data.get("spec") or {},
        generated_code=data.get("generated_code"),
    )


@router.put("/screens/{slug}", response_model=ScreenResponse)
async def update_screen(
    project_id: uuid.UUID,
    slug: str,
    body: ScreenPayload,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_update)),
    db: AsyncSession = Depends(get_db),
) -> ScreenResponse:
    await _ensure_project_exists(db, project_id)
    path = _screen_path(slug)
    if not await _workspace_service.file_exists(project_id, path):
        raise HTTPException(status_code=404, detail="Screen not found")

    data = body.model_dump()
    await _write_json(project_id, path, data)
    return ScreenResponse(
        project_id=project_id,
        slug=slug,
        path=path,
        **data,
    )


@router.delete("/screens/{slug}", status_code=204)
async def delete_screen(
    project_id: uuid.UUID,
    slug: str,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_update)),
    db: AsyncSession = Depends(get_db),
) -> None:
    await _ensure_project_exists(db, project_id)
    path = _screen_path(slug)
    if not await _workspace_service.file_exists(project_id, path):
        raise HTTPException(status_code=404, detail="Screen not found")
    await _workspace_service.delete_file(project_id, path)
