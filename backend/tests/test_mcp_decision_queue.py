"""Tests for ``core.mcp.decision_queue`` — per-decision asyncio.Event registry.

The MCP ``decision.wait`` tool blocks the BSGateway run thread until
the founder resolves the decision via the existing
``POST /api/v1/decisions/{id}/resolve`` API. This module is the
in-process bridge: ``register`` creates an Event, ``notify`` fires it,
``wait_for`` blocks the caller (with timeout) until the Event flips.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from backend.src.mcp.decision_queue import DecisionQueue, DecisionWaitTimeout


@pytest.mark.asyncio
async def test_notify_unblocks_pending_wait() -> None:
    queue = DecisionQueue()
    decision_id = uuid.uuid4()
    queue.register(decision_id)

    async def _wait() -> dict | None:
        return await queue.wait_for(decision_id, timeout_seconds=5.0)

    waiter = asyncio.create_task(_wait())
    await asyncio.sleep(0.01)
    assert not waiter.done()

    queue.notify(decision_id, result={"choice": "yes", "notes": "go for it"})
    res = await asyncio.wait_for(waiter, timeout=1.0)
    assert res == {"choice": "yes", "notes": "go for it"}


@pytest.mark.asyncio
async def test_wait_times_out_when_no_notify() -> None:
    queue = DecisionQueue()
    decision_id = uuid.uuid4()
    queue.register(decision_id)

    with pytest.raises(DecisionWaitTimeout):
        await queue.wait_for(decision_id, timeout_seconds=0.05)


@pytest.mark.asyncio
async def test_late_register_then_immediate_notify_unblocks() -> None:
    """Race: notify() arrives before register() (e.g. founder resolves while
    run is still being prepared). The result is buffered and the
    subsequent wait_for picks it up immediately."""
    queue = DecisionQueue()
    decision_id = uuid.uuid4()

    queue.notify(decision_id, result={"choice": "fast"})
    queue.register(decision_id)
    res = await queue.wait_for(decision_id, timeout_seconds=1.0)

    assert res == {"choice": "fast"}


@pytest.mark.asyncio
async def test_unregistered_wait_raises() -> None:
    queue = DecisionQueue()
    with pytest.raises(LookupError):
        await queue.wait_for(uuid.uuid4(), timeout_seconds=0.1)


@pytest.mark.asyncio
async def test_notify_unregistered_decision_buffers_for_late_register() -> None:
    """Same as the late-register case but with a different decision id —
    confirms the buffer is keyed correctly."""
    queue = DecisionQueue()
    a = uuid.uuid4()
    b = uuid.uuid4()

    queue.notify(b, result={"choice": "b-resolved"})
    queue.register(b)

    res = await queue.wait_for(b, timeout_seconds=1.0)
    assert res == {"choice": "b-resolved"}

    # ``a`` is still untouched
    queue.register(a)
    with pytest.raises(DecisionWaitTimeout):
        await queue.wait_for(a, timeout_seconds=0.05)


@pytest.mark.asyncio
async def test_concurrent_waiters_on_same_decision_all_unblock() -> None:
    """Multiple awaiters on one decision are uncommon (single CLI per
    run) but the queue should not silently drop duplicates."""
    queue = DecisionQueue()
    decision_id = uuid.uuid4()
    queue.register(decision_id)

    waiters = [asyncio.create_task(queue.wait_for(decision_id, timeout_seconds=2.0)) for _ in range(3)]
    await asyncio.sleep(0.01)
    queue.notify(decision_id, result={"choice": "broadcast"})

    results = await asyncio.gather(*waiters)
    assert results == [{"choice": "broadcast"}] * 3


@pytest.mark.asyncio
async def test_buffered_results_pruned_after_ttl() -> None:
    """Orphan notifies (founder resolves on a run that already timed out)
    must not leak — the buffered result drops after TTL on the next
    notify or register call."""
    queue = DecisionQueue(result_ttl_seconds=0.05)
    orphan = uuid.uuid4()
    queue.notify(orphan, result={"choice": "orphan"})
    assert orphan in queue._results

    await asyncio.sleep(0.06)

    # Trigger a prune via an unrelated notify.
    fresh = uuid.uuid4()
    queue.notify(fresh, result={"choice": "fresh"})

    assert orphan not in queue._results
    assert orphan not in queue._results_ts
    assert fresh in queue._results


@pytest.mark.asyncio
async def test_active_run_results_not_pruned_even_when_old() -> None:
    """An entry whose Event is registered (run still alive) must NOT be
    pruned even if it's older than TTL — the run is just slow."""
    queue = DecisionQueue(result_ttl_seconds=0.05)
    decision_id = uuid.uuid4()
    queue.register(decision_id)
    queue.notify(decision_id, result={"choice": "slow"})

    await asyncio.sleep(0.06)

    # Trigger a prune via an unrelated notify.
    queue.notify(uuid.uuid4(), result={"choice": "unrelated"})
    assert decision_id in queue._results, "registered decision must survive prune"
