from __future__ import annotations

import re
import unicodedata
from typing import Optional
from uuid import UUID

from bsvibe_auth import BSVibeUser
from backend.src import models, schemas
from backend.src.core.auth import Permission, require_permission
from backend.src.repositories.phase_repository import PhaseRepository
from backend.src.repositories.project_repository import ProjectRepository
from backend.src.storage.database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

# -- Helpers -------------------------------------------------------------------


def slugify(value: str) -> str:
    """Convert a string to a URL-friendly slug."""
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^\w\s-]", "", value.lower())
    return re.sub(r"[-\s]+", "-", value).strip("-")


class PhaseUpdate(BaseModel):
    """Local schema for partial phase updates."""

    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[schemas.PhaseStatus] = None


# -- Router --------------------------------------------------------------------

router = APIRouter(prefix="/api/v1/projects", tags=["projects"], redirect_slashes=False)


# -- Project Endpoints ---------------------------------------------------------


@router.post("", response_model=schemas.ProjectResponse, status_code=201)
async def create_project(
    project_data: schemas.ProjectCreate,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_create)),
    db: AsyncSession = Depends(get_db),
) -> schemas.ProjectResponse:
    """Create a new project."""
    repo = ProjectRepository(db)

    project = models.Project(
        name=project_data.name,
        description=project_data.description,
        repo_path=project_data.repo_path,
    )
    await repo.add(project)
    await repo.commit()

    # Reload with phases eagerly loaded
    project = await repo.get_by_id(project.id)
    return schemas.ProjectResponse.model_validate(project)


@router.get("", response_model=list[schemas.ProjectResponse])
async def list_projects(
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    _auth: BSVibeUser = Depends(require_permission(Permission.project_read)),
    db: AsyncSession = Depends(get_db),
) -> list[schemas.ProjectResponse]:
    """List all projects with pagination."""
    repo = ProjectRepository(db)
    projects = await repo.list_all(limit=limit, offset=offset)
    return [schemas.ProjectResponse.model_validate(p) for p in projects]


@router.get("/{project_id}", response_model=schemas.ProjectResponse)
async def get_project(
    project_id: UUID,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_read)),
    db: AsyncSession = Depends(get_db),
) -> schemas.ProjectResponse:
    """Get a project by ID."""
    repo = ProjectRepository(db)
    project = await repo.get_by_id(project_id)

    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    # Sort phases by order before returning
    project.phases.sort(key=lambda p: p.order)
    return schemas.ProjectResponse.model_validate(project)


@router.patch("/{project_id}", response_model=schemas.ProjectResponse)
async def update_project(
    project_id: UUID,
    project_data: schemas.ProjectUpdate,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_update)),
    db: AsyncSession = Depends(get_db),
) -> schemas.ProjectResponse:
    """Update a project (partial update)."""
    repo = ProjectRepository(db)
    project = await repo.get_by_id(project_id)

    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    update_data = project_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if field == "status" and value is not None:
            setattr(project, field, models.ProjectStatus(value))
        else:
            setattr(project, field, value)

    await repo.commit()

    # Reload with phases eagerly loaded
    project = await repo.get_by_id(project_id)
    return schemas.ProjectResponse.model_validate(project)


@router.delete("/{project_id}", response_model=schemas.DeleteResponse)
async def delete_project(
    project_id: UUID,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_delete)),
    db: AsyncSession = Depends(get_db),
) -> schemas.DeleteResponse:
    """Delete a project and all associated phases, tasks, and design sessions."""
    repo = ProjectRepository(db)
    project = await repo.get_by_id(project_id)

    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    await repo.delete(project)
    await repo.commit()

    return schemas.DeleteResponse(detail="Project deleted")


@router.post("/batch-delete", response_model=schemas.BatchDeleteResponse)
async def batch_delete_projects(
    body: schemas.BatchDeleteRequest,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_delete)),
    db: AsyncSession = Depends(get_db),
) -> schemas.BatchDeleteResponse:
    """Delete multiple projects by IDs."""
    from sqlalchemy import delete as sa_delete

    result = await db.execute(sa_delete(models.Project).where(models.Project.id.in_(body.ids)))
    await db.commit()
    deleted: int = result.rowcount if result.rowcount and result.rowcount > 0 else 0  # type: ignore[attr-defined]
    return schemas.BatchDeleteResponse(deleted=deleted)


# -- Phase Endpoints -----------------------------------------------------------


@router.post("/{project_id}/phases", response_model=schemas.PhaseResponse, status_code=201)
async def create_phase(
    project_id: UUID,
    phase_data: schemas.PhaseCreate,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_create)),
    db: AsyncSession = Depends(get_db),
) -> schemas.PhaseResponse:
    """Create a new phase for a project."""
    project_repo = ProjectRepository(db)
    phase_repo = PhaseRepository(db)

    # Verify project exists
    if not await project_repo.exists(project_id):
        raise HTTPException(status_code=404, detail="Project not found")

    # Auto-generate branch name
    branch_name = f"phase/{slugify(phase_data.name)}"

    # Auto-calculate order (max existing order + 1)
    next_order = await phase_repo.get_next_order(project_id)

    phase = models.Phase(
        project_id=project_id,
        name=phase_data.name,
        description=phase_data.description,
        branch_name=branch_name,
        order=next_order,
    )
    await phase_repo.add(phase)
    await phase_repo.commit()
    await phase_repo.refresh(phase)

    return schemas.PhaseResponse.model_validate(phase)


@router.get("/{project_id}/phases", response_model=list[schemas.PhaseResponse])
async def list_phases(
    project_id: UUID,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_read)),
    db: AsyncSession = Depends(get_db),
) -> list[schemas.PhaseResponse]:
    """List all phases for a project, ordered by phase order."""
    project_repo = ProjectRepository(db)
    phase_repo = PhaseRepository(db)

    # Verify project exists
    if not await project_repo.exists(project_id):
        raise HTTPException(status_code=404, detail="Project not found")

    phases = await phase_repo.list_by_project(project_id)
    return [schemas.PhaseResponse.model_validate(p) for p in phases]


@router.patch("/phases/{phase_id}", response_model=schemas.PhaseResponse)
async def update_phase(
    phase_id: UUID,
    phase_data: PhaseUpdate,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_update)),
    db: AsyncSession = Depends(get_db),
) -> schemas.PhaseResponse:
    """Update a phase (partial update)."""
    phase_repo = PhaseRepository(db)
    phase = await phase_repo.get_by_id(phase_id)

    if phase is None:
        raise HTTPException(status_code=404, detail="Phase not found")

    update_data = phase_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if field == "name" and value is not None:
            setattr(phase, field, value)
            # Update branch_name when name changes
            phase.branch_name = f"phase/{slugify(value)}"
        elif field == "status" and value is not None:
            setattr(phase, field, models.PhaseStatus(value))
        else:
            setattr(phase, field, value)

    await phase_repo.commit()
    await phase_repo.refresh(phase)

    return schemas.PhaseResponse.model_validate(phase)
