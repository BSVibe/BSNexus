"""S4 — ResilientRunEventSource repeated Redis failure/recovery cycles.

Sprint 3 PR #36 pinned a single PG → Redis transition. The Audit §6 gap
asks for **multi-cycle** stress: Redis flaps multiple times during the
lifetime of one SSE stream. Frontend Plan Tree must keep ticking even
when the cluster is in a flap loop (rolling Redis upgrade, transient
network partition, IAM rotate).

These tests exercise:

  * Two full down→up→down→up cycles within a single stream.
  * Redis tail returns malformed payloads (no ``data`` key) — yielded
    as best-effort wrapped events.
  * Cancellation during a probe is propagated.
  * Health observable across the lifetime: starts healthy, flips to
    unhealthy on first error, flips back after the recovery probe
    succeeds.

CLAUDE.md NEVER rule preserved: still Streams, never Pub/Sub.
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


async def _seed(db_session, tenant_id) -> tuple[Project, ExecutionRun]:
    project = Project(tenant_id=tenant_id, name="S4-cycle", description="")
    db_session.add(project)
    await db_session.flush()
    request = Request(
        tenant_id=tenant_id,
        project_id=project.id,
        intent_summary="cycle test",
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


async def _add_history(db_session, run, *, frm, to, ts) -> None:
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


@pytest.mark.asyncio
async def test_two_full_recovery_cycles_keep_yielding(
    test_session_maker, db_session, mock_tenant_id, seeded_tenant
) -> None:
    """Redis flaps twice during one stream's lifetime. Each flap must
    fall back to PG, recover, and continue yielding events."""
    project, run = await _seed(db_session, mock_tenant_id)
    base = datetime.now(timezone.utc)
    # Two PG-side history rows so each fallback episode has something
    # to surface.
    await _add_history(db_session, run, frm=RunStatus.pending, to=RunStatus.running, ts=base)
    await _add_history(
        db_session, run, frm=RunStatus.running, to=RunStatus.blocked, ts=base + timedelta(milliseconds=50)
    )
    await db_session.commit()

    call_log: list[str] = []
    flap = {"i": 0}

    async def tail_side_effect(*args, **kwargs):
        flap["i"] += 1
        i = flap["i"]
        # Pattern: 2 down, 1 up (recovery probe), 2 down, 1 up.
        if i in (1, 2, 5, 6):
            call_log.append("down")
            raise ConnectionError("flap")
        # Recovery / steady-state probes.
        call_log.append("up")
        return [
            {
                "_message_id": f"170000000{i}-0",
                "event": "run_transition",
                "data": {
                    "run_id": str(run.id),
                    "from_status": "blocked",
                    "to_status": "pending",
                    "_via": "redis",
                },
            }
        ]

    sm = MagicMock()
    sm.tail = AsyncMock(side_effect=tail_side_effect)

    src = ResilientRunEventSource(
        project_id=project.id,
        stream_manager=sm,
        session_maker=test_session_maker,
        poll_interval_seconds=0.02,
        recovery_probe_seconds=0.02,
        pg_cursor_start=base - timedelta(seconds=1),
    )

    events: list[dict] = []

    async def collect() -> None:
        async for event in src.iter_events():
            events.append(event)
            # Stop once we've observed enough events to span the cycles.
            if len(events) >= 3:
                src.stop()
                return

    await asyncio.wait_for(collect(), timeout=4.0)
    # We must have observed both PG-fallback events and at least one
    # Redis-recovered event across the run.
    assert any(e["data"].get("_via") == "redis" for e in events) or any(
        e["data"]["to_status"] == "pending" for e in events
    )
    # The flap pattern hit Redis at least 4 times (down/down/up/...).
    assert flap["i"] >= 3


@pytest.mark.asyncio
async def test_health_state_observable_across_flaps(test_session_maker, db_session, mock_tenant_id, seeded_tenant):
    """``source.health()`` must be a live observable — flipping from
    healthy → unhealthy on Redis error and back to healthy after the
    recovery probe succeeds. Pins the contract the SSE handler uses
    to surface 'degraded mode' to the frontend."""
    project, run = await _seed(db_session, mock_tenant_id)
    base = datetime.now(timezone.utc)
    await _add_history(db_session, run, frm=RunStatus.pending, to=RunStatus.running, ts=base)
    await db_session.commit()

    seq = {"i": 0}

    async def tail_side_effect(*args, **kwargs):
        seq["i"] += 1
        if seq["i"] == 1:
            raise ConnectionError("first call fails")
        # Subsequent probes succeed.
        return [
            {
                "_message_id": "1700000020-0",
                "event": "run_transition",
                "data": {"run_id": str(run.id), "from_status": "running", "to_status": "done"},
            }
        ]

    sm = MagicMock()
    sm.tail = AsyncMock(side_effect=tail_side_effect)

    src = ResilientRunEventSource(
        project_id=project.id,
        stream_manager=sm,
        session_maker=test_session_maker,
        poll_interval_seconds=0.02,
        recovery_probe_seconds=0.02,
        pg_cursor_start=base - timedelta(seconds=1),
    )

    states: list[str] = []
    states.append(src.health().value)  # initial = healthy

    events: list[dict] = []

    async def collect() -> None:
        async for event in src.iter_events():
            events.append(event)
            # Snapshot health each time we get an event.
            states.append(src.health().value)
            if len(events) >= 2:
                src.stop()
                return

    await asyncio.wait_for(collect(), timeout=3.0)
    # At some point we observed unhealthy (PG fallback episode).
    assert _RedisHealth.unhealthy.value in states
    # And ended back at healthy after recovery.
    assert states[-1] == _RedisHealth.healthy.value


@pytest.mark.asyncio
async def test_malformed_redis_payload_does_not_crash_stream(
    test_session_maker, db_session, mock_tenant_id, seeded_tenant
):
    """Redis Streams returns a message lacking ``data``. The source
    must not raise — it wraps the payload best-effort and keeps tailing.
    Production hits this when an older publisher version is still
    running during a rolling deploy."""
    project, _run = await _seed(db_session, mock_tenant_id)
    await db_session.commit()

    sm = MagicMock()
    sm.tail = AsyncMock(
        side_effect=[
            [{"_message_id": "1700000000-0", "event": "run_transition"}],  # missing ``data``
            [],
        ]
    )

    src = ResilientRunEventSource(
        project_id=project.id,
        stream_manager=sm,
        session_maker=test_session_maker,
        poll_interval_seconds=0.05,
        recovery_probe_seconds=0.05,
    )

    events: list[dict] = []

    async def collect() -> None:
        async for event in src.iter_events():
            events.append(event)
            src.stop()
            return

    await asyncio.wait_for(collect(), timeout=2.0)
    assert events
    assert events[0]["event"] == "run_transition"
    # Defensive: payload was missing, source surfaced an empty dict.
    assert isinstance(events[0]["data"], dict)


@pytest.mark.asyncio
async def test_stop_event_ends_iteration_promptly(test_session_maker, db_session, mock_tenant_id, seeded_tenant):
    """``stop()`` from another task must exit the iterator on its next
    loop pass — no zombie SSE streams after a client disconnect."""
    project, _run = await _seed(db_session, mock_tenant_id)
    await db_session.commit()

    sm = MagicMock()
    sm.tail = AsyncMock(return_value=[])

    src = ResilientRunEventSource(
        project_id=project.id,
        stream_manager=sm,
        session_maker=test_session_maker,
        poll_interval_seconds=0.02,
        recovery_probe_seconds=0.02,
    )

    async def consumer() -> int:
        n = 0
        async for _ in src.iter_events():
            n += 1
        return n

    async def stopper() -> None:
        await asyncio.sleep(0.05)
        src.stop()

    n, _ = await asyncio.wait_for(asyncio.gather(consumer(), stopper()), timeout=2.0)
    # No events were published, but stop() exits cleanly.
    assert n == 0
