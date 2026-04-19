"""Agent CRUD API — manage AI agents with dynamic roles and org chart hierarchy."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from backend.src.core.agent_activity import (
    has_online_worker,
    resolve_agent_runtime_status,
    resolve_agent_status_dot,
)
from backend.src.core.tenant_context import get_tenant_id
from backend.src.models import Agent, ExecutorConfig, Task
from backend.src.repositories.agent_repository import AgentRepository
from backend.src.schemas.agent import AgentCreate, AgentOrgChartResponse, AgentResponse, AgentUpdate, CurrentTaskBrief
from backend.src.storage.database import get_db

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


def _agent_to_response(
    agent: Agent,
    *,
    has_online_worker: bool = False,
    default_executor_type: str | None = None,
    current_task: Task | None = None,
    is_processing: bool = False,
) -> AgentResponse:
    response = AgentResponse.model_validate(agent)
    if agent.executor_config_id is None and default_executor_type is not None:
        response.executor_type = default_executor_type
    response.status = resolve_agent_runtime_status(
        agent,
        current_task=current_task,
        online_worker_available=has_online_worker,
    )
    response.dot = resolve_agent_status_dot(
        agent,
        current_task=current_task,
        online_worker_available=has_online_worker,
        is_processing=is_processing,
    )
    if is_processing and not current_task:
        response.activity = "처리 중..."
    if current_task is not None:
        response.current_task = CurrentTaskBrief(
            id=current_task.id,
            title=current_task.title,
            status=current_task.status.value,
        )
        response.activity = current_task.title
    return response


async def _tenant_default_executor_type(
    db: AsyncSession, tenant_id: uuid.UUID
) -> str | None:
    """Return the executor_type of the tenant's current default config, or None."""
    result = await db.execute(
        select(ExecutorConfig.executor_type).where(
            ExecutorConfig.tenant_id == tenant_id,
            ExecutorConfig.is_default.is_(True),
        ).limit(1)
    )
    return result.scalar_one_or_none()


async def _has_online_worker(db: AsyncSession, tenant_id: uuid.UUID) -> bool:
    """True if any active worker in the tenant is online."""
    return await has_online_worker(db, tenant_id)


async def _resolve_executor_type(
    db: AsyncSession, config_id: uuid.UUID | None, tenant_id: uuid.UUID
) -> str:
    """Resolve executor_type from config_id, falling back to tenant default."""
    if config_id:
        result = await db.execute(select(ExecutorConfig).where(ExecutorConfig.id == config_id))
        ec = result.scalar_one_or_none()
        if ec:
            return ec.executor_type
    # Fall back to tenant default
    result = await db.execute(
        select(ExecutorConfig).where(
            ExecutorConfig.tenant_id == tenant_id,
            ExecutorConfig.is_default.is_(True),
        ).limit(1)
    )
    ec = result.scalar_one_or_none()
    return ec.executor_type if ec else "generic_llm"


@router.post("", response_model=AgentResponse, status_code=201)
async def create_agent(
    body: AgentCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> AgentResponse:
    repo = AgentRepository(db)
    executor_type = await _resolve_executor_type(db, body.executor_config_id, tenant_id)
    agent = Agent(
        tenant_id=tenant_id,
        name=body.name,
        role=body.role,
        title=body.title,
        job_description=body.job_description,
        executor_config_id=body.executor_config_id,
        executor_type=executor_type,
        executor_config=body.executor_config,
        system_prompt=body.system_prompt,
        skills=body.skills,
        capabilities=body.capabilities,
        parent_agent_id=body.parent_agent_id,
        heartbeat_interval_seconds=body.heartbeat_interval_seconds,
        heartbeat_enabled=body.heartbeat_enabled,
        monthly_budget_cents=body.monthly_budget_cents,
    )
    await repo.add(agent)
    await repo.commit()
    await repo.refresh(agent)
    online = await _has_online_worker(db, tenant_id)
    return _agent_to_response(agent, has_online_worker=online)


async def _running_tasks_by_agent(
    db: AsyncSession, agent_ids: list[uuid.UUID],
) -> dict[uuid.UUID, Task]:
    """Return one running task per agent (first match)."""
    from backend.src.models import TaskStatus

    if not agent_ids:
        return {}
    result = await db.execute(
        select(Task).where(
            Task.status == TaskStatus.running,
            Task.assigned_agent_id.in_(agent_ids),
        )
    )
    by_agent: dict[uuid.UUID, Task] = {}
    for task in result.scalars().all():
        if task.assigned_agent_id is not None and task.assigned_agent_id not in by_agent:
            by_agent[task.assigned_agent_id] = task
    return by_agent


@router.get("", response_model=list[AgentResponse])
async def list_agents(
    request: Request,
    active_only: bool = True,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> list[AgentResponse]:
    repo = AgentRepository(db)
    agents = await repo.list_by_tenant(tenant_id, active_only=active_only, limit=limit, offset=offset)
    online = await _has_online_worker(db, tenant_id)
    default_type = await _tenant_default_executor_type(db, tenant_id)
    agent_ids = [a.id for a in agents]
    running = await _running_tasks_by_agent(db, agent_ids)

    # Check Redis for transient processing state
    redis = getattr(request.app.state, "redis", None)
    processing_ids: set[uuid.UUID] = set()
    if redis:
        for aid in agent_ids:
            val = await redis.get(f"agent:processing:{aid}")
            if val:
                processing_ids.add(aid)

    return [
        _agent_to_response(
            a,
            has_online_worker=online,
            default_executor_type=default_type,
            current_task=running.get(a.id),
            is_processing=a.id in processing_ids,
        )
        for a in agents
    ]


@router.get("/org-chart", response_model=list[AgentOrgChartResponse])
async def get_org_chart(
    request: Request,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> list[AgentOrgChartResponse]:
    """Return org chart as a tree of root agents with nested children."""
    repo = AgentRepository(db)
    all_agents = await repo.list_by_tenant(tenant_id, active_only=True, limit=500)
    online = await _has_online_worker(db, tenant_id)
    default_type = await _tenant_default_executor_type(db, tenant_id)
    agent_ids = [a.id for a in all_agents]
    running = await _running_tasks_by_agent(db, agent_ids)

    # Build tree
    by_parent: dict[uuid.UUID | None, list[Agent]] = {}
    for agent in all_agents:
        by_parent.setdefault(agent.parent_agent_id, []).append(agent)

    def _build_tree(parent_id: uuid.UUID | None) -> list[AgentOrgChartResponse]:
        children = by_parent.get(parent_id, [])
        return [
            AgentOrgChartResponse(
                agent=_agent_to_response(
                    child,
                    has_online_worker=online,
                    default_executor_type=default_type,
                    current_task=running.get(child.id),
                ),
                children=_build_tree(child.id),
            )
            for child in children
        ]

    return _build_tree(None)


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> AgentResponse:
    repo = AgentRepository(db)
    agent = await repo.get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    online = await _has_online_worker(db, tenant_id)
    default_type = await _tenant_default_executor_type(db, tenant_id)
    running = await _running_tasks_by_agent(db, [agent.id])
    return _agent_to_response(
        agent,
        has_online_worker=online,
        default_executor_type=default_type,
        current_task=running.get(agent.id),
    )


@router.patch("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: uuid.UUID,
    body: AgentUpdate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> AgentResponse:
    repo = AgentRepository(db)
    agent = await repo.get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    update_data = body.model_dump(exclude_unset=True)
    online = await _has_online_worker(db, tenant_id)
    default_type = await _tenant_default_executor_type(db, tenant_id)
    if not update_data:
        return _agent_to_response(
            agent, has_online_worker=online, default_executor_type=default_type
        )

    # Sync executor_type when executor_config_id changes
    if "executor_config_id" in update_data:
        update_data["executor_type"] = await _resolve_executor_type(
            db, update_data["executor_config_id"], tenant_id
        )

    updated = await repo.update_fields(agent_id, **update_data)
    await repo.commit()
    if not updated:
        raise HTTPException(status_code=404, detail="Agent not found after update")
    return _agent_to_response(
        updated, has_online_worker=online, default_executor_type=default_type
    )


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(agent_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> None:
    repo = AgentRepository(db)
    agent = await repo.get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    await repo.delete(agent)
    await repo.commit()


@router.post("/{agent_id}/heartbeat", status_code=200)
async def trigger_agent_heartbeat(
    agent_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Trigger an immediate heartbeat for an agent."""
    repo = AgentRepository(db)
    agent = await repo.get_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not agent.heartbeat_enabled:
        raise HTTPException(status_code=400, detail="Heartbeat not enabled for this agent")

    # Publish heartbeat event if stream_manager is available
    stream_manager = getattr(request.app.state, "stream_manager", None)
    if stream_manager is not None:
        from backend.src.core.heartbeat import HeartbeatScheduler

        scheduler = HeartbeatScheduler(db_session_factory=None, stream_manager=stream_manager)
        await scheduler.trigger_immediate(agent_id)

    return {"status": "triggered", "agent_id": str(agent_id)}
