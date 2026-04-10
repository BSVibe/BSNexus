"""Import an existing project into BSNexus.

Replaces the legacy ``/api/v1/architect/migrate/stream`` endpoint.
The new flow is composed from two pluggable abstractions:

  - ImportSource    (LocalPath, GitRemote, Tarball)
  - WorkspaceStorage (Local, Git)

The endpoint orchestrates them, persists a Project row, and stores the
analyzer summary as ``Project.repo_path`` plus a metadata column. The
heavy lifting (LLM-based analysis and Phase/Task generation) lives on
top of this scaffold and runs as a normal worker task — there is no
"Architect agent" anymore.
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Literal

from bsvibe_auth import BSVibeUser
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src import models
from backend.src.config import settings as app_settings
from backend.src.core.auth import Permission, require_permission
from backend.src.core.import_sources import (
    GitRemoteSource,
    ImportSource,
    LocalPathSource,
    TarballSource,
)
from backend.src.core.tenant_context import get_tenant_id
from backend.src.core.workspace_storage import (
    GitWorkspaceStorage,
    LocalWorkspaceStorage,
    WorkspaceStorage,
)
from backend.src.storage.database import get_db

router = APIRouter(prefix="/api/v1/projects/import", tags=["import"])


# ── Schemas ──────────────────────────────────────────────────────────


SourceType = Literal["local", "git", "tarball"]
StorageType = Literal["local", "git"]


class ImportProjectRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = ""
    source_type: SourceType
    source_uri: str = Field(..., description="Path, git URL, or uploaded archive id")
    source_branch: str | None = None
    storage_type: StorageType = "local"
    storage_remote_url: str | None = None
    storage_branch: str = "main"


class ImportProjectResponse(BaseModel):
    project_id: uuid.UUID
    workspace_dir: str
    files_count: int
    detected_language: str | None = None
    has_git: bool
    remote_url: str | None = None


# ── Helpers ──────────────────────────────────────────────────────────


def _make_source(req: ImportProjectRequest) -> ImportSource:
    if req.source_type == "local":
        return LocalPathSource(req.source_uri)
    if req.source_type == "git":
        return GitRemoteSource(req.source_uri, branch=req.source_branch)
    if req.source_type == "tarball":
        return TarballSource(req.source_uri)
    raise HTTPException(status_code=400, detail=f"Unknown source_type: {req.source_type}")


def _make_storage(req: ImportProjectRequest, root: Path) -> WorkspaceStorage:
    if req.storage_type == "local":
        return LocalWorkspaceStorage(root)
    if req.storage_type == "git":
        if not req.storage_remote_url:
            raise HTTPException(status_code=400, detail="storage_remote_url required for git storage")
        return GitWorkspaceStorage(root, req.storage_remote_url, branch=req.storage_branch)
    raise HTTPException(status_code=400, detail=f"Unknown storage_type: {req.storage_type}")


def _workspace_root() -> Path:
    workspace_root = getattr(app_settings, "workspace_root", None)
    if workspace_root:
        return Path(workspace_root)
    return Path(tempfile.gettempdir()) / "bsnexus" / "workspaces"


# ── Endpoint ─────────────────────────────────────────────────────────


_ANALYZER_PROMPT = (
    "A new codebase has just been imported into this project's workspace at "
    "`{workspace}`. Detected language: {language}. {file_count} files total. "
    "Run the analyzer workflow described in your system prompt and report "
    "back. End with [CREATE_TASK] markers for any high-priority gaps."
)


async def _seed_analyzer_task(
    db: AsyncSession,
    project: models.Project,
    metadata,
    tenant_id: uuid.UUID,
) -> None:
    """Create a kickoff task for the Analyzer agent (if one is registered)."""
    analyzer_result = await db.execute(
        select(models.Agent).where(
            models.Agent.tenant_id == tenant_id,
            models.Agent.role == "analyzer",
            models.Agent.is_active.is_(True),
        ).limit(1)
    )
    analyzer = analyzer_result.scalar_one_or_none()
    if analyzer is None:
        # No analyzer agent yet — skip seeding. The user can apply the
        # 'specialists' template and re-run the import.
        return

    # Make sure the project has a phase to attach the task to.
    phase = models.Phase(
        project_id=project.id,
        name="Phase 1: Analyze",
        description="Initial codebase audit",
        branch_name="phase/analyze",
        order=1,
        status=models.PhaseStatus.active,
    )
    db.add(phase)
    await db.flush()

    prompt = _ANALYZER_PROMPT.format(
        workspace=project.workspace_dir,
        language=metadata.detected_language or "unknown",
        file_count=metadata.files_count,
    )
    task = models.Task(
        project_id=project.id,
        phase_id=phase.id,
        title="Analyze imported codebase",
        description=prompt,
        status=models.TaskStatus.pending,
        priority=models.TaskPriority.high,
        task_type=models.TaskType.chore,
        source=models.TaskSource.llm,
        agent_id=analyzer.id,
        worker_prompt={"prompt": prompt},
        qa_prompt={"prompt": "Verify the analyzer report covers languages, frameworks, architecture, and risks."},
    )
    db.add(task)


@router.post("", response_model=ImportProjectResponse, status_code=201)
async def import_project(
    body: ImportProjectRequest,
    _auth: BSVibeUser = Depends(require_permission(Permission.project_create)),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> ImportProjectResponse:
    """Pull an existing codebase into BSNexus and create the Project row.

    On success the endpoint also seeds an Analyzer task in Phase 1 so
    the global dispatcher will hand the codebase audit to whichever
    worker matches the analyzer role on the next tick.
    """
    source = _make_source(body)
    storage = _make_storage(body, _workspace_root())

    project_id = uuid.uuid4()
    staging = _workspace_root() / "_staging" / str(project_id)
    try:
        metadata = await source.fetch(staging)
        location = await storage.provision(project_id, staging)
    finally:
        if staging.exists():
            import shutil

            shutil.rmtree(staging, ignore_errors=True)

    project = models.Project(
        id=project_id,
        name=body.name,
        description=body.description,
        repo_path=str(location.local_path),
        workspace_type=models.WorkspaceType.local_import,
        workspace_dir=str(location.local_path),
        github_repo_url=location.remote_url,
        status=models.ProjectStatus.active,
    )
    db.add(project)
    await db.flush()

    await _seed_analyzer_task(db, project, metadata, tenant_id)
    await db.commit()
    await db.refresh(project)

    return ImportProjectResponse(
        project_id=project.id,
        workspace_dir=str(location.local_path),
        files_count=metadata.files_count,
        detected_language=metadata.detected_language,
        has_git=metadata.has_git,
        remote_url=location.remote_url,
    )
