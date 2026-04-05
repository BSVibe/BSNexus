"""Heartbeat scheduler — periodically wakes agents to check and execute pending tasks."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.models import Agent

logger = structlog.get_logger(__name__)


class HeartbeatScheduler:
    """Manages periodic agent heartbeats.

    Each agent with heartbeat_enabled=True gets woken at its configured interval.
    On heartbeat, the agent checks for ready tasks and triggers execution.
    """

    def __init__(self, db_session_factory: object) -> None:
        self._db_session_factory = db_session_factory
        self._running = False
        self._tasks: dict[uuid.UUID, asyncio.Task[None]] = {}

    async def start(self) -> None:
        """Start the heartbeat scheduler loop."""
        self._running = True
        logger.info("heartbeat_scheduler_started")

        while self._running:
            try:
                await self._check_agents()
            except Exception:
                logger.error("heartbeat_scheduler_error", exc_info=True)
            await asyncio.sleep(30)  # Check every 30 seconds

    async def stop(self) -> None:
        """Stop the scheduler and cancel all heartbeat tasks."""
        self._running = False
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()
        logger.info("heartbeat_scheduler_stopped")

    async def _check_agents(self) -> None:
        """Check which agents need a heartbeat trigger."""
        async for db in self._db_session_factory():  # type: ignore[union-attr]
            result = await db.execute(
                select(Agent).where(
                    Agent.heartbeat_enabled.is_(True),
                    Agent.is_active.is_(True),
                    Agent.heartbeat_interval_seconds.isnot(None),
                )
            )
            agents = list(result.scalars().all())

            now = datetime.now(timezone.utc)
            for agent in agents:
                if self._should_trigger(agent, now):
                    await self._trigger_heartbeat(agent, db, now)

    @staticmethod
    def _should_trigger(agent: Agent, now: datetime) -> bool:
        """Check if enough time has passed since last heartbeat."""
        if agent.heartbeat_interval_seconds is None:
            return False
        if agent.last_heartbeat_at is None:
            return True
        elapsed = (now - agent.last_heartbeat_at).total_seconds()
        return elapsed >= agent.heartbeat_interval_seconds

    async def _trigger_heartbeat(self, agent: Agent, db: AsyncSession, now: datetime) -> None:
        """Trigger a heartbeat for an agent."""
        logger.info("heartbeat_triggered", agent_id=str(agent.id), agent_name=agent.name)

        # Check budget
        if agent.monthly_budget_cents is not None and agent.current_month_spent_cents >= agent.monthly_budget_cents:
            logger.warning(
                "heartbeat_budget_exceeded",
                agent_id=str(agent.id),
                spent=agent.current_month_spent_cents,
                limit=agent.monthly_budget_cents,
            )
            await db.execute(
                update(Agent).where(Agent.id == agent.id).values(status="budget_exceeded")
            )
            await db.commit()
            return

        # Update last heartbeat
        await db.execute(
            update(Agent).where(Agent.id == agent.id).values(last_heartbeat_at=now, status="online")
        )
        await db.commit()

        # TODO: Trigger pending task execution for this agent
        # This will be wired when the orchestrator integrates heartbeat

    async def trigger_immediate(self, agent_id: uuid.UUID) -> None:
        """Trigger an immediate heartbeat for an agent (e.g., task assignment, @-mention)."""
        logger.info("heartbeat_immediate_trigger", agent_id=str(agent_id))
        # TODO: Wire to execution loop
