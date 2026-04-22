"""ToolContext factory — resolves workspace paths for all workspace types.

The factory creates a ToolContext once per agent turn. It resolves
``workspace_path`` based on the project's workspace_type so that
tool implementations never need to know about storage backends.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.models.project import Project, WorkspaceType
from backend.src.tools.base import ToolContext

WORKSPACE_BASE_DIR = os.environ.get("WORKSPACE_BASE_DIR", "./data/workspaces")


def _resolve_workspace_path(project: Project) -> Path:
    """Resolve the absolute workspace path for a project.

    - server_managed / github_connected: WORKSPACE_BASE_DIR / {project.id}
    - local_import: project.workspace_dir (arbitrary user path)
    """
    if project.workspace_type == WorkspaceType.local_import and project.workspace_dir:
        return Path(project.workspace_dir)
    # server_managed and github_connected both use the managed directory.
    # project.workspace_dir may already point here, but we derive from
    # the canonical base to stay consistent with WorkspaceService.
    return Path(WORKSPACE_BASE_DIR) / str(project.id)


async def create_tool_context(
    *,
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    agent_name: str,
    tenant_id: uuid.UUID,
    db_session_factory: Callable[..., Any],
    redis: Any | None = None,
) -> ToolContext:
    """Build a ToolContext by looking up the project and resolving its workspace.

    Opens a short-lived session to read the project row, then closes it.
    """
    async with db_session_factory() as session:
        session: AsyncSession
        result = await session.execute(
            select(Project).where(Project.id == project_id)
        )
        project = result.scalar_one_or_none()
        if project is None:
            raise ValueError(f"Project {project_id} not found")

        workspace_path = _resolve_workspace_path(project)
        workspace_type = project.workspace_type.value if hasattr(project.workspace_type, "value") else str(project.workspace_type)

    return ToolContext(
        project_id=project_id,
        workspace_path=workspace_path,
        workspace_type=workspace_type,
        agent_id=agent_id,
        agent_name=agent_name,
        tenant_id=tenant_id,
        db_session_factory=db_session_factory,
        redis=redis,
    )


def create_tool_context_sync(
    *,
    project_id: uuid.UUID,
    workspace_path: Path,
    workspace_type: str,
    agent_id: uuid.UUID,
    agent_name: str,
    tenant_id: uuid.UUID,
    db_session_factory: Callable[..., Any],
    redis: Any | None = None,
) -> ToolContext:
    """Create a ToolContext without DB lookup (for workers that already know the path)."""
    return ToolContext(
        project_id=project_id,
        workspace_path=workspace_path,
        workspace_type=workspace_type,
        agent_id=agent_id,
        agent_name=agent_name,
        tenant_id=tenant_id,
        db_session_factory=db_session_factory,
        redis=redis,
    )
