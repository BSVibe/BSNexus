"""Tests for heartbeat → task dispatch integration."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.heartbeat import HeartbeatScheduler
from backend.src.models import Agent, Tenant

_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


@pytest_asyncio.fixture(autouse=True)
async def _seed_tenant(db_session):
    t = Tenant(id=_TENANT_ID, name="Test", slug="test", owner_user_id="user-1")
    db_session.add(t)
    await db_session.flush()
    await db_session.commit()


async def _create_agent(
    db: AsyncSession,
    name: str = "Agent",
    heartbeat_enabled: bool = True,
    heartbeat_interval_seconds: int = 60,
    last_heartbeat_at: datetime | None = None,
    monthly_budget_cents: int | None = None,
    current_month_spent_cents: int = 0,
) -> Agent:
    agent = Agent(
        tenant_id=_TENANT_ID,
        name=name,
        role="dev",
        heartbeat_enabled=heartbeat_enabled,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
        last_heartbeat_at=last_heartbeat_at,
        monthly_budget_cents=monthly_budget_cents,
        current_month_spent_cents=current_month_spent_cents,
    )
    db.add(agent)
    await db.flush()
    await db.refresh(agent)
    return agent


@pytest.mark.asyncio
async def test_trigger_heartbeat_publishes_ready_tasks(db_session, test_session_maker):
    """Heartbeat publishes ready tasks assigned to agent to the stream."""
    agent = await _create_agent(db_session, last_heartbeat_at=datetime.now(timezone.utc) - timedelta(hours=2))
    await db_session.commit()

    mock_stream = AsyncMock()
    mock_stream.publish = AsyncMock(return_value="msg-1")

    scheduler = HeartbeatScheduler(db_session_factory=_make_factory(test_session_maker), stream_manager=mock_stream)

    # Manually trigger heartbeat
    async with test_session_maker() as db:
        await scheduler._trigger_heartbeat(agent, db, datetime.now(timezone.utc))

    # Stream should have been called with agent wakeup event
    mock_stream.publish.assert_called_once()
    call_args = mock_stream.publish.call_args
    assert call_args[0][0] == "tasks:escalation"
    assert call_args[0][1]["event"] == "agent_heartbeat"
    assert call_args[0][1]["agent_id"] == str(agent.id)


@pytest.mark.asyncio
async def test_trigger_heartbeat_skips_budget_exceeded(db_session, test_session_maker):
    """Heartbeat does not dispatch when budget is exceeded."""
    agent = await _create_agent(
        db_session,
        monthly_budget_cents=1000,
        current_month_spent_cents=1000,
        last_heartbeat_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    await db_session.commit()

    mock_stream = AsyncMock()
    scheduler = HeartbeatScheduler(db_session_factory=_make_factory(test_session_maker), stream_manager=mock_stream)

    async with test_session_maker() as db:
        await scheduler._trigger_heartbeat(agent, db, datetime.now(timezone.utc))

    # Should NOT publish — budget exceeded
    mock_stream.publish.assert_not_called()


@pytest.mark.asyncio
async def test_trigger_immediate_publishes_event(db_session, test_session_maker):
    """trigger_immediate publishes an immediate heartbeat event."""
    agent = await _create_agent(db_session)
    await db_session.commit()

    mock_stream = AsyncMock()
    mock_stream.publish = AsyncMock(return_value="msg-1")

    scheduler = HeartbeatScheduler(db_session_factory=_make_factory(test_session_maker), stream_manager=mock_stream)
    await scheduler.trigger_immediate(agent.id)

    mock_stream.publish.assert_called_once()
    call_args = mock_stream.publish.call_args
    assert call_args[0][1]["event"] == "agent_heartbeat_immediate"
    assert call_args[0][1]["agent_id"] == str(agent.id)


@pytest.mark.asyncio
async def test_should_trigger_first_heartbeat():
    """First heartbeat (no last_heartbeat_at) should trigger."""
    agent = MagicMock()
    agent.heartbeat_interval_seconds = 60
    agent.last_heartbeat_at = None
    assert HeartbeatScheduler._should_trigger(agent, datetime.now(timezone.utc)) is True


@pytest.mark.asyncio
async def test_should_not_trigger_too_soon():
    """Heartbeat within interval should not trigger."""
    agent = MagicMock()
    agent.heartbeat_interval_seconds = 3600
    agent.last_heartbeat_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    assert HeartbeatScheduler._should_trigger(agent, datetime.now(timezone.utc)) is False


def _make_factory(session_maker):
    """Wrap session_maker as async generator factory matching get_db pattern."""
    async def factory():
        async with session_maker() as session:
            yield session
    return factory
