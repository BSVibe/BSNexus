from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from backend.src.models import Project
from backend.src.repositories.base import BaseRepository


class ProjectRepository(BaseRepository):
    """Data access layer for Project entities."""

    async def get_by_id(self, project_id: uuid.UUID, *, load_phases: bool = True) -> Project | None:
        """Get a project by ID with optional phase loading."""
        query = select(Project).where(Project.id == project_id)
        if load_phases:
            query = query.options(selectinload(Project.phases))
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def list_all(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Project]:
        """List projects with pagination."""
        query = (
            select(Project)
            .options(selectinload(Project.phases))
            .order_by(Project.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def exists(self, project_id: uuid.UUID) -> bool:
        """Check if a project exists."""
        result = await self.db.execute(select(Project.id).where(Project.id == project_id))
        return result.scalar_one_or_none() is not None
