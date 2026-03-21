from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from backend.src.models import Task, TaskPriority, TaskStatus, task_dependencies
from backend.src.repositories.base import BaseRepository

# Priority ordering for scheduling (critical first)
PRIORITY_ORDER: dict[TaskPriority, int] = {
    TaskPriority.critical: 0,
    TaskPriority.high: 1,
    TaskPriority.medium: 2,
    TaskPriority.low: 3,
}


class TaskRepository(BaseRepository):
    """Data access layer for Task entities."""

    async def get_by_id(
        self,
        task_id: uuid.UUID,
        *,
        load_depends: bool = True,
        load_history: bool = False,
    ) -> Task | None:
        """Get a task by ID with optional relationship loading."""
        options = []
        if load_depends:
            options.append(selectinload(Task.depends_on))
        if load_history:
            options.append(selectinload(Task.history))
        result = await self.db.execute(select(Task).where(Task.id == task_id).options(*options))
        return result.scalar_one_or_none()

    async def list_by_project(
        self,
        project_id: uuid.UUID,
        *,
        status: Optional[TaskStatus] = None,
        phase_id: Optional[uuid.UUID] = None,
        priority: Optional[TaskPriority] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Task]:
        """List tasks for a project with optional filters."""
        query = select(Task).where(Task.project_id == project_id)
        if status is not None:
            query = query.where(Task.status == status)
        if phase_id is not None:
            query = query.where(Task.phase_id == phase_id)
        if priority is not None:
            query = query.where(Task.priority == priority)
        query = query.order_by(Task.created_at.desc()).limit(limit).offset(offset)
        query = query.options(selectinload(Task.depends_on))
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def add_dependencies(self, task_id: uuid.UUID, dependency_ids: list[uuid.UUID]) -> None:
        """Insert task dependency relationships."""
        for dep_id in dependency_ids:
            await self.db.execute(task_dependencies.insert().values(task_id=task_id, dependency_id=dep_id))

    async def validate_dependencies_exist(self, dependency_ids: list[uuid.UUID]) -> list[uuid.UUID]:
        """Return list of dependency IDs that do NOT exist."""
        if not dependency_ids:
            return []
        result = await self.db.execute(select(Task.id).where(Task.id.in_(dependency_ids)))
        found = set(result.scalars().all())
        return [d for d in dependency_ids if d not in found]

    async def detect_circular_dependency(self, task_id: uuid.UUID, depends_on: list[uuid.UUID]) -> bool:
        """Detect circular dependencies using DFS."""
        visited: set[uuid.UUID] = set()

        async def dfs(current_id: uuid.UUID) -> bool:
            if current_id == task_id:
                return True
            if current_id in visited:
                return False
            visited.add(current_id)
            result = await self.db.execute(
                select(task_dependencies.c.dependency_id).where(task_dependencies.c.task_id == current_id)
            )
            for dep_id in result.scalars().all():
                if await dfs(dep_id):
                    return True
            return False

        for dep_id in depends_on:
            if dep_id == task_id:
                return True
            if await dfs(dep_id):
                return True
        return False

    async def get_dependency_ids(self, task_id: uuid.UUID) -> list[uuid.UUID]:
        """Get all dependency IDs for a task."""
        result = await self.db.execute(
            select(task_dependencies.c.dependency_id).where(task_dependencies.c.task_id == task_id)
        )
        return list(result.scalars().all())

    async def get_incomplete_dependency_count(self, task_id: uuid.UUID) -> int:
        """Count dependencies that are not in DONE status."""
        dep_ids = await self.get_dependency_ids(task_id)
        if not dep_ids:
            return 0
        result = await self.db.execute(
            select(Task.id).where(
                Task.id.in_(dep_ids),
                Task.status != TaskStatus.done,
            )
        )
        return len(result.scalars().all())

    async def check_dependencies_met(self, task_id: uuid.UUID) -> bool:
        """Check if all dependency tasks are in DONE status."""
        return await self.get_incomplete_dependency_count(task_id) == 0

    async def find_waiting_dependents(self, task_id: uuid.UUID) -> list[Task]:
        """Find WAITING tasks that depend on the given task."""
        result = await self.db.execute(
            select(Task).where(
                Task.id.in_(select(task_dependencies.c.task_id).where(task_dependencies.c.dependency_id == task_id)),
                Task.status == TaskStatus.waiting,
            )
        )
        return list(result.scalars().all())

    async def count_by_status(self, project_id: uuid.UUID) -> dict[str, int]:
        """Count tasks grouped by status for a project."""
        result = await self.db.execute(
            select(Task.status, func.count(Task.id)).where(Task.project_id == project_id).group_by(Task.status)
        )
        counts: dict[str, int] = {}
        for status, count in result.all():
            counts[status.value if hasattr(status, "value") else str(status)] = count
        return counts

    async def list_ready_by_priority(self, project_id: uuid.UUID) -> list[Task]:
        """Get READY tasks sorted by priority (critical first) then creation time."""
        result = await self.db.execute(
            select(Task)
            .where(Task.project_id == project_id, Task.status == TaskStatus.ready)
            .order_by(Task.created_at.asc())
        )
        tasks = list(result.scalars().all())
        tasks.sort(key=lambda t: PRIORITY_ORDER.get(t.priority, 99))
        return tasks

    async def count_active_tasks(self, project_id: uuid.UUID) -> int:
        """Count tasks in in_progress or review status for a project."""
        active_statuses = [TaskStatus.in_progress, TaskStatus.review]
        result = await self.db.execute(
            select(func.count(Task.id)).where(Task.project_id == project_id, Task.status.in_(active_statuses))
        )
        return result.scalar_one()

    async def list_waiting_in_phase(self, phase_id: uuid.UUID) -> list[Task]:
        """Get waiting tasks within a specific phase."""
        result = await self.db.execute(select(Task).where(Task.phase_id == phase_id, Task.status == TaskStatus.waiting))
        return list(result.scalars().all())

    async def list_incomplete_in_phase(self, phase_id: uuid.UUID) -> list[Task]:
        """Get all incomplete (non-done) tasks within a phase."""
        result = await self.db.execute(
            select(Task)
            .where(Task.phase_id == phase_id, Task.status != TaskStatus.done)
            .options(selectinload(Task.depends_on))
        )
        return list(result.scalars().all())

    async def list_done_in_phase(self, phase_id: uuid.UUID) -> list[Task]:
        """Get all done tasks within a phase."""
        result = await self.db.execute(select(Task).where(Task.phase_id == phase_id, Task.status == TaskStatus.done))
        return list(result.scalars().all())

    async def hard_delete(self, task_id: uuid.UUID) -> None:
        """Permanently delete a task and its dependencies/history (CASCADE)."""
        await self.db.execute(delete(Task).where(Task.id == task_id))

    async def hard_delete_many(self, task_ids: list[uuid.UUID]) -> int:
        """Permanently delete multiple tasks. Returns count deleted."""
        if not task_ids:
            return 0
        result = await self.db.execute(delete(Task).where(Task.id.in_(task_ids)))
        return result.rowcount or 0

    async def clear_dependencies(self, task_id: uuid.UUID) -> None:
        """Remove all dependency relationships for a task (both directions)."""
        await self.db.execute(task_dependencies.delete().where(task_dependencies.c.task_id == task_id))
        await self.db.execute(task_dependencies.delete().where(task_dependencies.c.dependency_id == task_id))
