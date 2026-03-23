from __future__ import annotations

import uuid
from typing import Optional

from bsvibe_auth import BSVibeUser
from backend.src import models, schemas
from backend.src.core.auth import Permission, require_permission
from backend.src.core.state_machine import TaskStateMachine
from backend.src.models import Task
from backend.src.repositories.task_repository import TaskRepository
from backend.src.storage.database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])

state_machine = TaskStateMachine()


# -- Helpers -------------------------------------------------------------------


def build_task_response(task: Task) -> schemas.TaskResponse:
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


# -- Endpoints ----------------------------------------------------------------


@router.post("/", response_model=schemas.TaskResponse, status_code=201)
async def create_task(
    task_data: schemas.TaskCreate,
    request: Request,
    _auth: BSVibeUser = Depends(require_permission(Permission.task_create)),
    db: AsyncSession = Depends(get_db),
) -> schemas.TaskResponse:
    """Create a new task."""
    repo = TaskRepository(db)

    # Validate depends_on tasks exist
    if task_data.depends_on:
        missing = await repo.validate_dependencies_exist(task_data.depends_on)
        if missing:
            raise HTTPException(status_code=400, detail=f"Dependency tasks not found: {missing}")

        # Detect circular dependencies (use a temporary UUID since the task doesn't exist yet)
        temp_id = uuid.uuid4()
        if await repo.detect_circular_dependency(temp_id, task_data.depends_on):
            raise HTTPException(status_code=400, detail="Circular dependency detected")

    # Determine initial status based on phase and dependencies
    if task_data.depends_on:
        initial_status = models.TaskStatus.waiting
    else:
        # Only set to ready if the phase is active
        from backend.src.repositories.phase_repository import PhaseRepository

        phase_repo = PhaseRepository(db)
        phase = await phase_repo.get_by_id(task_data.phase_id)
        if phase and phase.status == models.PhaseStatus.active:
            initial_status = models.TaskStatus.ready
        else:
            initial_status = models.TaskStatus.waiting

    # Create Task ORM object
    task = Task(
        project_id=task_data.project_id,
        phase_id=task_data.phase_id,
        title=task_data.title,
        description=task_data.description,
        priority=models.TaskPriority(task_data.priority.value),
        task_type=models.TaskType(task_data.task_type.value),
        worker_prompt={"prompt": task_data.worker_prompt},
        qa_prompt={"prompt": task_data.qa_prompt},
        status=initial_status,
        version=1,
    )
    await repo.add(task)

    # Add dependency relationships
    if task_data.depends_on:
        await repo.add_dependencies(task.id, task_data.depends_on)

    await repo.commit()

    # Reload with relationships
    created_task = await repo.get_by_id(task.id)
    if created_task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return build_task_response(created_task)


@router.get("/{task_id}", response_model=schemas.TaskResponse)
async def get_task(
    task_id: uuid.UUID,
    include_history: bool = Query(False),
    _auth: BSVibeUser = Depends(require_permission(Permission.task_read)),
    db: AsyncSession = Depends(get_db),
) -> schemas.TaskResponse:
    """Get a task by ID."""
    repo = TaskRepository(db)
    task = await repo.get_by_id(task_id, load_depends=True, load_history=include_history)

    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    return build_task_response(task)


@router.patch("/{task_id}", response_model=schemas.TaskResponse)
async def update_task(
    task_id: uuid.UUID,
    task_data: schemas.TaskUpdate,
    _auth: BSVibeUser = Depends(require_permission(Permission.task_update)),
    db: AsyncSession = Depends(get_db),
) -> schemas.TaskResponse:
    """Update a task (only in waiting or ready status)."""
    repo = TaskRepository(db)
    task = await repo.get_by_id(task_id, load_depends=True)

    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    # Optimistic locking check
    if task_data.expected_version is not None:
        if task.version != task_data.expected_version:
            raise HTTPException(
                status_code=409,
                detail=f"Version conflict: expected {task_data.expected_version}, current {task.version}",
            )

    if task.status not in (models.TaskStatus.waiting, models.TaskStatus.ready):
        raise HTTPException(
            status_code=400,
            detail="Task can only be updated in waiting or ready status",
        )

    update_data = task_data.model_dump(exclude_unset=True, exclude={"expected_version"})
    for field, value in update_data.items():
        if field == "priority" and value is not None:
            setattr(task, field, models.TaskPriority(value))
        else:
            setattr(task, field, value)
    task.version += 1

    await repo.commit()

    # Reload with depends_on
    updated_task = await repo.get_by_id(task_id, load_depends=True)
    if updated_task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return build_task_response(updated_task)


@router.post("/{task_id}/transition", response_model=schemas.TransitionResponse)
async def transition_task(
    task_id: uuid.UUID,
    transition: schemas.TaskTransition,
    request: Request,
    _auth: BSVibeUser = Depends(require_permission(Permission.task_transition)),
    db: AsyncSession = Depends(get_db),
) -> schemas.TransitionResponse:
    """Transition a task to a new status."""
    repo = TaskRepository(db)
    task = await repo.get_by_id(task_id, load_depends=False)

    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    # Optimistic locking check
    if transition.expected_version is not None:
        if task.version != transition.expected_version:
            raise HTTPException(
                status_code=409,
                detail=f"Version conflict: expected {transition.expected_version}, current {task.version}",
            )

    # Convert schema TaskStatus to model TaskStatus
    model_status = models.TaskStatus(transition.new_status.value)

    # Get stream_manager from app state
    stream_manager = getattr(request.app.state, "stream_manager", None)

    # Store previous status for response
    previous_status = task.status

    try:
        await state_machine.transition(
            task,
            model_status,
            reason=transition.reason,
            actor=transition.actor,
            db_session=db,
            stream_manager=stream_manager,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    await repo.commit()

    return schemas.TransitionResponse(
        task_id=task.id,
        status=schemas.TaskStatus(task.status.value),
        previous_status=schemas.TaskStatus(previous_status.value),
        transition={
            "from": previous_status.value,
            "to": task.status.value,
            "reason": transition.reason,
            "actor": transition.actor,
        },
    )


@router.get("/by-project/{project_id}", response_model=list[schemas.TaskResponse])
async def list_project_tasks(
    project_id: uuid.UUID,
    status: Optional[str] = Query(None),
    phase_id: Optional[uuid.UUID] = Query(None),
    priority: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    _auth: BSVibeUser = Depends(require_permission(Permission.task_read)),
    db: AsyncSession = Depends(get_db),
) -> list[schemas.TaskResponse]:
    """List tasks for a project with optional filters."""
    repo = TaskRepository(db)

    try:
        status_filter = models.TaskStatus(status) if status is not None else None
        priority_filter = models.TaskPriority(priority) if priority is not None else None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    tasks = await repo.list_by_project(
        project_id,
        status=status_filter,
        phase_id=phase_id,
        priority=priority_filter,
        limit=limit,
        offset=offset,
    )

    return [build_task_response(task) for task in tasks]
