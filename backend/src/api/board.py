from __future__ import annotations

import asyncio
import json
import uuid
import structlog
from typing import AsyncGenerator

import redis.asyncio as aioredis
from backend.src import models, schemas
from backend.src.repositories.task_repository import TaskRepository
from backend.src.storage.database import get_db
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/board", tags=["board"])


def _build_task_response(task: models.Task) -> schemas.TaskResponse:
    """Build TaskResponse from Task ORM object."""
    dep_ids = [dep.id for dep in task.depends_on] if task.depends_on else []
    return schemas.TaskResponse(
        id=task.id,
        project_id=task.project_id,
        phase_id=task.phase_id,
        title=task.title,
        description=task.description,
        status=schemas.TaskStatus(task.status.value),
        priority=schemas.TaskPriority(task.priority.value),
        task_type=schemas.TaskType(task.task_type.value),
        source=schemas.TaskSource(task.source.value),
        parent_task_id=task.parent_task_id,
        worker_prompt=task.worker_prompt,
        qa_prompt=task.qa_prompt,
        branch_name=task.branch_name,
        commit_hash=task.commit_hash,
        qa_result=task.qa_result,
        output_path=task.output_path,
        error_message=task.error_message,
        retry_count=task.retry_count,
        max_retries=task.max_retries,
        qa_feedback_history=task.qa_feedback_history,
        version=task.version,
        created_at=task.created_at,
        updated_at=task.updated_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
        depends_on=dep_ids,
    )


async def _get_board_data(
    project_id: uuid.UUID,
    db: AsyncSession,
) -> dict:
    """Build board data dict for a project."""
    repo = TaskRepository(db)
    tasks = await repo.list_by_project(project_id, limit=500)

    # Group tasks by status — redesign tasks go into a separate list
    kanban_statuses = [s for s in models.TaskStatus if s != models.TaskStatus.redesign]
    columns: dict[str, list] = {status.value: [] for status in kanban_statuses}
    redesign_tasks: list[dict] = []
    for task in tasks:
        task_resp = _build_task_response(task)
        if task.status == models.TaskStatus.redesign:
            redesign_tasks.append(task_resp.model_dump(mode="json"))
        else:
            columns[task.status.value].append(task_resp.model_dump(mode="json"))

    # Stats
    status_counts = await repo.count_by_status(project_id)
    total = sum(status_counts.values())
    stats: dict[str, int] = {"total": total}
    for status in models.TaskStatus:
        stats[status.value] = status_counts.get(status.value, 0)

    # Phase lookup: id -> {name, order, status}
    phase_result = await db.execute(
        select(models.Phase.id, models.Phase.name, models.Phase.order, models.Phase.status).where(
            models.Phase.project_id == project_id
        )
    )
    phases = {
        str(row.id): {"name": row.name, "order": row.order, "status": row.status.value} for row in phase_result.all()
    }

    return {
        "project_id": str(project_id),
        "columns": {status: {"tasks": task_list} for status, task_list in columns.items()},
        "stats": stats,
        "phases": phases,
        "redesign_tasks": redesign_tasks,
    }


@router.get("/{project_id}")
async def get_board(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get full board state for a project."""
    return await _get_board_data(project_id, db)


async def _board_event_generator(
    project_id: str,
    redis_client: aioredis.Redis,
) -> AsyncGenerator[dict, None]:
    """Subscribe to events:board stream and yield matching events."""
    last_id = "$"
    while True:
        try:
            messages = await redis_client.xread(
                streams={"events:board": last_id},
                count=10,
                block=5000,
            )
            if messages:
                for _stream_name, entries in messages:
                    for msg_id, data in entries:
                        last_id = msg_id
                        event_project_id = data.get("project_id", "")
                        if event_project_id == project_id:
                            yield {
                                "data": json.dumps(data),
                            }
        except asyncio.CancelledError:
            break
        except Exception:
            logger.warning("board_sse_error", project_id=project_id, exc_info=True)
            await asyncio.sleep(1)


@router.get("/{project_id}/events")
async def board_events(
    project_id: uuid.UUID,
    request: Request,
) -> EventSourceResponse:
    """SSE stream for board events."""
    redis_client: aioredis.Redis = request.app.state.redis
    return EventSourceResponse(_board_event_generator(str(project_id), redis_client))
