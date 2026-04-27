"""Projects API — tenant-scoped CRUD."""

from __future__ import annotations

import uuid

import structlog
from bsvibe_audit.events.nexus import ProjectCreated
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.audit import actor_from_user, resource_project, safe_emit
from backend.src.core.auth import get_current_user
from backend.src.core.tenant_context import get_tenant_id
from backend.src.models import Project
from backend.src.schemas import ProjectCreate, ProjectResponse, ProjectUpdate
from backend.src.storage.database import get_db

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


async def _get_project_for_tenant(db: AsyncSession, project_id: uuid.UUID, tenant_id: uuid.UUID) -> Project:
    stmt = select(Project).where(Project.id == project_id, Project.tenant_id == tenant_id)
    project = (await db.execute(stmt)).scalar_one_or_none()
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> list[Project]:
    stmt = select(Project).where(Project.tenant_id == tenant_id).order_by(Project.created_at.desc())
    return list((await db.execute(stmt)).scalars())


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate,
    user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> Project:
    project = Project(
        tenant_id=tenant_id,
        name=payload.name,
        description=payload.description,
        bsage_workspace_id=payload.bsage_workspace_id,
        bsupervisor_policy_id=payload.bsupervisor_policy_id,
    )
    db.add(project)
    # Flush so ``project.id`` is populated for the audit resource ref,
    # then emit the audit event in the same transaction as the INSERT.
    # The single ``commit()`` below makes both rows durable atomically
    # — exactly the outbox-pattern guarantee BSVibe_Audit_Design.md §3.1
    # asks for.
    await db.flush()

    await safe_emit(
        ProjectCreated(
            actor=actor_from_user(user),
            tenant_id=str(tenant_id),
            resource=resource_project(project.id),
            data={
                "name": project.name,
                "description": project.description,
            },
        ),
        session=db,
    )

    await db.commit()
    await db.refresh(project)
    logger.info("project_created", project_id=str(project.id), tenant_id=str(tenant_id))
    return project


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: uuid.UUID,
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> Project:
    return await _get_project_for_tenant(db, project_id, tenant_id)


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> Project:
    project = await _get_project_for_tenant(db, project_id, tenant_id)
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(project, key, value)
    await db.commit()
    await db.refresh(project)
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: uuid.UUID,
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> None:
    project = await _get_project_for_tenant(db, project_id, tenant_id)
    await db.delete(project)
    await db.commit()
