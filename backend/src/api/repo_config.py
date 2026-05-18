"""Per-project repo binding admin API (G8.0).

One repo binding per project, stored inline on the ``projects`` row
(``github_repo_url`` / ``github_branch`` / ``github_token_encrypted``).
No separate table — those columns existed in the schema but were
unused before G8.

Routes (flat per CLAUDE.md A3):
  GET    /api/v1/repo-config?project_id=<uuid>
  PUT    /api/v1/repo-config?project_id=<uuid>
  DELETE /api/v1/repo-config?project_id=<uuid>

``token`` on PUT is tri-state:
  - omitted → preserve existing encrypted value
  - null    → clear the secret
  - string  → encrypt + replace

Cross-tenant lookups 404 (never leak existence).

Binding a repo flips ``workspace_type`` to ``github_connected`` (and a
DELETE reverts it to ``server_managed``). Phase 3 made the binding the
project's *source of truth*: the orchestrator clones a github_connected
project's repo into the workspace so the work LLM edits real files.
The founder just "connects a repo"; the ``workspace_type`` knob stays
invisible. (Pre-Phase-3 the binding was delivery-target-only and left
``workspace_type`` untouched.)
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.config import settings as app_settings
from backend.src.core.auth import get_current_user, require_permission
from backend.src.core.encryption import EncryptionManager
from backend.src.core.tenant_context import get_tenant_id
from backend.src.models.project import Project, WorkspaceType
from backend.src.schemas.repo_config import (
    RepoConfigResponse,
    RepoConfigUpdate,
    redacted,
)
from backend.src.storage.database import get_db

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/repo-config", tags=["repo-config"])


def _encryption() -> EncryptionManager:
    return EncryptionManager(app_settings.encryption_key)


async def _load_project(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> Project:
    stmt = select(Project).where(
        Project.id == project_id,
        Project.tenant_id == tenant_id,
    )
    project = (await db.execute(stmt)).scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    return project


@router.get(
    "",
    response_model=RepoConfigResponse | None,
    dependencies=[Depends(require_permission("bsnexus.repo_config.read"))],
)
async def get_repo_config(
    project_id: uuid.UUID = Query(...),
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> RepoConfigResponse | None:
    project = await _load_project(db, project_id=project_id, tenant_id=tenant_id)
    return redacted(project)


@router.put(
    "",
    response_model=RepoConfigResponse,
    dependencies=[Depends(require_permission("bsnexus.repo_config.write"))],
)
async def upsert_repo_config(
    payload: RepoConfigUpdate,
    project_id: uuid.UUID = Query(...),
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> RepoConfigResponse:
    project = await _load_project(db, project_id=project_id, tenant_id=tenant_id)
    updates = payload.model_dump(exclude_unset=True)

    project.github_repo_url = payload.repo_url
    project.github_branch = payload.branch
    if "token" in updates:
        if updates["token"]:
            project.github_token_encrypted = _encryption().encrypt_value(updates["token"])
        else:
            project.github_token_encrypted = None

    # Binding a repo IS connecting the project to it — the orchestrator
    # clones a github_connected project's repo into the workspace so the
    # work LLM edits real files. The founder "connects a repo"; the
    # workspace_type knob stays invisible.
    project.workspace_type = WorkspaceType.github_connected

    await db.commit()
    await db.refresh(project)
    logger.info(
        "repo_config_upserted",
        project_id=str(project.id),
        tenant_id=str(tenant_id),
        has_token=bool(project.github_token_encrypted),
    )
    result = redacted(project)
    assert result is not None
    return result


@router.delete(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("bsnexus.repo_config.delete"))],
)
async def delete_repo_config(
    project_id: uuid.UUID = Query(...),
    _user=Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> Response:
    project = await _load_project(db, project_id=project_id, tenant_id=tenant_id)
    project.github_repo_url = None
    project.github_branch = None
    project.github_token_encrypted = None
    # No repo left to clone — hand the project back to a managed
    # workspace. Only un-flip a project the binding itself set to
    # github_connected; leave server_managed / local_import untouched.
    if project.workspace_type == WorkspaceType.github_connected:
        project.workspace_type = WorkspaceType.server_managed
    await db.commit()
    logger.info("repo_config_cleared", project_id=str(project.id), tenant_id=str(tenant_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
