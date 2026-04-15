from __future__ import annotations

import uuid

from sqlalchemy import func, select

from backend.src.models import Phase, PhaseStatus, Task, TaskStatus
from backend.src.repositories.base import BaseRepository


class PhaseRepository(BaseRepository):
    """Data access layer for Phase entities."""

    async def get_by_id(self, phase_id: uuid.UUID) -> Phase | None:
        """Get a phase by ID."""
        result = await self.db.execute(select(Phase).where(Phase.id == phase_id))
        return result.scalar_one_or_none()

    async def list_by_project(self, project_id: uuid.UUID) -> list[Phase]:
        """List phases for a project ordered by order."""
        result = await self.db.execute(select(Phase).where(Phase.project_id == project_id).order_by(Phase.order.asc()))
        return list(result.scalars().all())

    async def get_next_order(self, project_id: uuid.UUID) -> int:
        """Calculate the next order value for a project."""
        result = await self.db.execute(
            select(func.coalesce(func.max(Phase.order), 0)).where(Phase.project_id == project_id)
        )
        return result.scalar_one() + 1

    async def get_active_phase(self, project_id: uuid.UUID) -> Phase | None:
        """Get the active phase for a project (at most one)."""
        result = await self.db.execute(
            select(Phase).where(Phase.project_id == project_id, Phase.status == PhaseStatus.active)
        )
        return result.scalar_one_or_none()

    async def get_first_pending_phase(self, project_id: uuid.UUID) -> Phase | None:
        """Get the pending phase with the lowest order value."""
        result = await self.db.execute(
            select(Phase)
            .where(Phase.project_id == project_id, Phase.status == PhaseStatus.pending)
            .order_by(Phase.order.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_next_pending_phase(self, project_id: uuid.UUID, current_order: int) -> Phase | None:
        """Get the next pending phase after the given order."""
        result = await self.db.execute(
            select(Phase)
            .where(Phase.project_id == project_id, Phase.status == PhaseStatus.pending, Phase.order > current_order)
            .order_by(Phase.order.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def count_incomplete_tasks(self, phase_id: uuid.UUID) -> int:
        """Count tasks in the phase that are not done."""
        result = await self.db.execute(
            select(func.count(Task.id)).where(Task.phase_id == phase_id, Task.status != TaskStatus.done)
        )
        return result.scalar_one()

    async def count_total_tasks(self, phase_id: uuid.UUID) -> int:
        """Count all tasks in the phase regardless of status."""
        result = await self.db.execute(
            select(func.count(Task.id)).where(Task.phase_id == phase_id)
        )
        return result.scalar_one()
