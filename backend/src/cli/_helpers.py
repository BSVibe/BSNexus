"""Shared CLI helpers."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession


@asynccontextmanager
async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Get an async DB session for CLI use."""
    from backend.src.storage.database import async_session

    async with async_session() as session:
        yield session


async def build_orchestrator() -> tuple[Any, Any]:
    """Build a PMOrchestrator for CLI use.

    Returns (orchestrator, db_session_factory).
    """
    from backend.src.config import settings
    from backend.src.core.executor import create_executor
    from backend.src.core.orchestrator import PMOrchestrator
    from backend.src.core.state_machine import TaskStateMachine
    from backend.src.core.task_runner import LocalTaskRunner
    from backend.src.queue.streams import RedisStreamManager
    from backend.src.storage.database import async_session
    from backend.src.storage.redis_client import get_redis

    redis = await get_redis()
    stream_manager = RedisStreamManager(redis)
    await stream_manager.initialize_streams()

    task_runner = LocalTaskRunner(create_executor(settings.executor_type))
    state_machine = TaskStateMachine()

    orchestrator = PMOrchestrator(
        stream_manager=stream_manager,
        task_runner=task_runner,
        state_machine=state_machine,
    )

    return orchestrator, async_session
