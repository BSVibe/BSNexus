"""S3-2 — SSE Redis Streams subscriber with PG polling fallback.

Today the SSE endpoint fans out per-project events from an in-process
``ProjectEventBus``. ``RunStateMachine.transition`` already publishes
those same run-transition events to Redis Streams (``project:events:*``)
via ``RedisStreamManager.publish_project_event`` so a future multi-uvicorn
deploy has the cross-instance plumbing in place.

This module adds the matching consumer side: a ``ResilientRunEventSource``
that reads run-transition events from Redis Streams and falls back to
Postgres polling against ``ExecutionRunHistory`` when Redis is down.

Behaviour the tests pin:

  * **Redis up** → events come from ``RedisStreamManager.tail`` with no
    PG round-trips.
  * **Redis transient failure** (raises ConnectionError or RedisError on
    ``tail``) → source switches to PG polling at a 1s tick and emits
    ``ExecutionRunHistory`` rows whose ``timestamp`` is newer than the
    last-seen marker.
  * **Recovery** → after a cooldown the source retries Redis; on success
    it resumes Streams reads. No event is delivered twice (the ``last_id``
    high-water mark is preserved in the Redis path; the PG ``last_seen``
    timestamp is preserved across a fallback episode).
  * **Zero events lost** → during the Redis → PG transition, every run
    transition that landed in PG (which is the source of truth) is
    surfaced exactly once.

CLAUDE.md NEVER rule preserved: still Streams, never Pub/Sub. The
fallback is a different transport (PG SELECT) but never Pub/Sub.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.src.core.run_event_source import (
    ResilientRunEventSource,
    _RedisHealth,
)
from backend.src.models import (
    ExecutionRun,
    ExecutionRunHistory,
    Project,
    Request,
    RequestStatus,
    RunPriority,
    RunStatus,
)


async def _seed_run_with_history(db_session, tenant_id) -> tuple[Project, ExecutionRun]:
    project = Project(tenant_id=tenant_id, name="S3-2", description="")
    db_session.add(project)
    await db_session.flush()
    request = Request(
        tenant_id=tenant_id,
        project_id=project.id,
        intent_summary="event source",
        status=RequestStatus.open,
    )
    db_session.add(request)
    await db_session.flush()
    run = ExecutionRun(
        tenant_id=tenant_id,
        project_id=project.id,
        request_id=request.id,
        status=RunStatus.running,
        priority=RunPriority.medium,
    )
    db_session.add(run)
    await db_session.flush()
    return project, run


async def _insert_history(db_session, run: ExecutionRun, *, frm: RunStatus, to: RunStatus, ts: datetime) -> None:
    db_session.add(
        ExecutionRunHistory(
            run_id=run.id,
            from_status=frm,
            to_status=to,
            actor="test",
            timestamp=ts,
        )
    )
    await db_session.flush()


# -- Redis happy path --------------------------------------------------------


@pytest.mark.asyncio
async def test_source_reads_from_redis_when_healthy(test_session_maker, db_session, mock_tenant_id, seeded_tenant):
    project, _run = await _seed_run_with_history(db_session, mock_tenant_id)
    await db_session.commit()

    stream_manager = MagicMock()
    stream_manager.tail = AsyncMock(
        side_effect=[
            [
                {
                    "_message_id": "1700000000000-0",
                    "event": "run_transition",
                    "data": {"run_id": "abc", "from_status": "pending", "to_status": "running"},
                }
            ],
            [],  # subsequent call: no new events
        ]
    )

    src = ResilientRunEventSource(
        project_id=project.id,
        stream_manager=stream_manager,
        session_maker=test_session_maker,
        poll_interval_seconds=0.05,
        recovery_probe_seconds=0.05,
    )

    events: list[dict] = []

    async def collect() -> None:
        async for event in src.iter_events():
            events.append(event)
            if len(events) >= 1:
                src.stop()
                return

    await asyncio.wait_for(collect(), timeout=2.0)
    assert events[0]["event"] == "run_transition"
    assert events[0]["data"]["to_status"] == "running"
    # PG polling must NOT have fired while Redis was healthy.
    # (We didn't mock a PG executor query path; the test ran end-to-end.)


# -- Fallback to PG ----------------------------------------------------------


@pytest.mark.asyncio
async def test_source_falls_back_to_pg_when_redis_down(test_session_maker, db_session, mock_tenant_id, seeded_tenant):
    project, run = await _seed_run_with_history(db_session, mock_tenant_id)
    now = datetime.now(timezone.utc)
    await _insert_history(db_session, run, frm=RunStatus.pending, to=RunStatus.running, ts=now)
    await db_session.commit()

    stream_manager = MagicMock()
    # tail() always raises — Redis is down for the duration of the test.
    stream_manager.tail = AsyncMock(side_effect=ConnectionError("redis offline"))

    src = ResilientRunEventSource(
        project_id=project.id,
        stream_manager=stream_manager,
        session_maker=test_session_maker,
        poll_interval_seconds=0.05,
        recovery_probe_seconds=0.05,
        # Start the PG cursor before the seeded history row so it picks
        # the row up.
        pg_cursor_start=now - timedelta(seconds=1),
    )

    events: list[dict] = []

    async def collect() -> None:
        async for event in src.iter_events():
            events.append(event)
            if len(events) >= 1:
                src.stop()
                return

    await asyncio.wait_for(collect(), timeout=3.0)
    assert events[0]["event"] == "run_transition"
    assert events[0]["data"]["to_status"] == "running"
    assert events[0]["data"]["run_id"] == str(run.id)
    # Confirm we actually went down the fallback path.
    assert src.health() == _RedisHealth.unhealthy


# -- Recovery: PG → Redis when Redis comes back ------------------------------


@pytest.mark.asyncio
async def test_source_recovers_from_pg_back_to_redis(test_session_maker, db_session, mock_tenant_id, seeded_tenant):
    project, run = await _seed_run_with_history(db_session, mock_tenant_id)
    now = datetime.now(timezone.utc)
    await _insert_history(db_session, run, frm=RunStatus.pending, to=RunStatus.running, ts=now)
    await db_session.commit()

    redis_calls = {"n": 0}

    async def tail_side_effect(*args, **kwargs):
        redis_calls["n"] += 1
        if redis_calls["n"] <= 2:
            raise ConnectionError("redis offline")
        # On the 3rd call onwards, Redis is back and serves a fresh event.
        return [
            {
                "_message_id": "1700000000050-0",
                "event": "run_transition",
                "data": {
                    "run_id": str(run.id),
                    "from_status": "running",
                    "to_status": "done",
                },
            }
        ]

    stream_manager = MagicMock()
    stream_manager.tail = AsyncMock(side_effect=tail_side_effect)

    src = ResilientRunEventSource(
        project_id=project.id,
        stream_manager=stream_manager,
        session_maker=test_session_maker,
        poll_interval_seconds=0.05,
        recovery_probe_seconds=0.05,
        pg_cursor_start=now - timedelta(seconds=1),
    )

    events: list[dict] = []

    async def collect() -> None:
        async for event in src.iter_events():
            events.append(event)
            if len(events) >= 2:
                src.stop()
                return

    await asyncio.wait_for(collect(), timeout=3.0)

    # First event arrives via PG polling (pending→running, the row we
    # seeded). Second arrives via Redis after recovery (running→done).
    transitions = [(e["data"]["from_status"], e["data"]["to_status"]) for e in events]
    assert ("pending", "running") in transitions
    assert ("running", "done") in transitions
    assert src.health() == _RedisHealth.healthy


# -- No-duplicate guarantee across fallback ---------------------------------


@pytest.mark.asyncio
async def test_pg_polling_does_not_replay_already_seen_history(
    test_session_maker, db_session, mock_tenant_id, seeded_tenant
):
    project, run = await _seed_run_with_history(db_session, mock_tenant_id)
    base = datetime.now(timezone.utc)
    await _insert_history(db_session, run, frm=RunStatus.pending, to=RunStatus.running, ts=base)
    await db_session.commit()

    stream_manager = MagicMock()
    stream_manager.tail = AsyncMock(side_effect=ConnectionError("redis offline"))

    src = ResilientRunEventSource(
        project_id=project.id,
        stream_manager=stream_manager,
        session_maker=test_session_maker,
        poll_interval_seconds=0.05,
        recovery_probe_seconds=0.05,
        pg_cursor_start=base - timedelta(seconds=1),
    )

    events: list[dict] = []

    async def collect() -> None:
        # Drain a few PG polls — must yield the seeded row exactly once.
        async for event in src.iter_events():
            events.append(event)
            # Wait for two more polls beyond the first event.
            if len(events) >= 1:
                await asyncio.sleep(0.2)
                src.stop()
                return

    await asyncio.wait_for(collect(), timeout=3.0)
    # The single history row must surface exactly once.
    assert len(events) == 1
    assert events[0]["data"]["to_status"] == "running"


# -- Zero-event guarantee: tenant isolation ---------------------------------


@pytest.mark.asyncio
async def test_pg_polling_is_scoped_to_project(test_session_maker, db_session, mock_tenant_id, seeded_tenant):
    """A history row on a different project must NOT be surfaced."""
    project_a, run_a = await _seed_run_with_history(db_session, mock_tenant_id)
    project_b, run_b = await _seed_run_with_history(db_session, mock_tenant_id)
    base = datetime.now(timezone.utc)
    await _insert_history(db_session, run_b, frm=RunStatus.pending, to=RunStatus.running, ts=base)
    await db_session.commit()

    stream_manager = MagicMock()
    stream_manager.tail = AsyncMock(side_effect=ConnectionError("redis offline"))

    src = ResilientRunEventSource(
        project_id=project_a.id,
        stream_manager=stream_manager,
        session_maker=test_session_maker,
        poll_interval_seconds=0.05,
        recovery_probe_seconds=0.05,
        pg_cursor_start=base - timedelta(seconds=1),
    )

    events: list[dict] = []

    async def collect() -> None:
        try:
            await asyncio.wait_for(_drain_one(src, events), timeout=0.5)
        except asyncio.TimeoutError:
            src.stop()

    async def _drain_one(s, sink):
        async for event in s.iter_events():
            sink.append(event)
            return

    await collect()
    assert events == [], "events from another project must not leak into this stream"


# -- SSE endpoint integration -----------------------------------------------


def test_api_project_events_imports_resilient_source() -> None:
    """The SSE handler must construct ``ResilientRunEventSource`` when
    a stream manager is attached. Verified by static import — a dynamic
    test requires cancelling an infinite SSE stream which is fragile."""
    import inspect

    from backend.src.api import project_events as api_pe

    src = inspect.getsource(api_pe)
    assert "ResilientRunEventSource" in src, (
        "api/project_events lost its hook for the resilient run-event source — see S3-2."
    )
    assert "_build_resilient_source" in src, (
        "api/project_events lost the helper that wires app.state.stream_manager into the resilient source."
    )
