"""S1-3 — H12 race-prone in-memory state must be guarded by ``asyncio.Lock``.

The tenant integration snapshot cache (``_cache``) in
``backend.src.core.integrations.config`` is read by every run dispatch
(``RunOrchestrator.dispatch_run`` → ``get_tenant_integration_snapshot``)
and mutated by:

  * Cache miss path — multiple concurrent first-touch requests for the
    same tenant decrypt the API key N times concurrently and write the
    same snapshot to the dict N times (thundering-herd: wasted decrypt
    work + transient inconsistency if a settings write lands between
    reads).

  * ``invalidate_tenant_cache`` — called from PATCH /integrations.

This test forces the race: it patches ``_load_rows`` with a coroutine
that awaits an Event before returning, kicks off N concurrent
``get_tenant_integration_snapshot`` calls for the same tenant, waits
for all of them to be parked inside the lock-protected critical section,
releases the Event, and asserts the loader ran exactly *once*.

Without the lock, the loader runs N times. With it, it runs once and
the rest get the cached value.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import MagicMock

import pytest

from backend.src.core.integrations import config as cfg_mod


@pytest.fixture(autouse=True)
def _reset_cache():
    """Each test starts with an empty cache."""
    cfg_mod._cache.clear()
    yield
    cfg_mod._cache.clear()


@pytest.mark.asyncio
async def test_concurrent_first_load_runs_loader_once(monkeypatch):
    """Thundering-herd guard: 10 concurrent reads of the same tenant
    must trigger ``_load_rows`` once, not 10 times."""
    tenant_id = uuid.uuid4()

    call_count = 0
    release = asyncio.Event()

    async def fake_load_rows(db, tid):  # noqa: ANN001
        nonlocal call_count
        call_count += 1
        # Park here long enough for all callers to queue up behind the
        # lock. If multiple loaders run concurrently, ``call_count``
        # will increment past 1 before any of them finish.
        await release.wait()
        return {}

    monkeypatch.setattr(cfg_mod, "_load_rows", fake_load_rows)

    db = MagicMock()
    fanout = 10

    async def call():
        return await cfg_mod.get_tenant_integration_snapshot(db, tenant_id)

    # Kick off the fanout, give the event loop a few ticks to schedule
    # all of them so they all reach the cache miss path before we let
    # the loader return.
    tasks = [asyncio.create_task(call()) for _ in range(fanout)]
    for _ in range(5):
        await asyncio.sleep(0)

    release.set()
    snapshots = await asyncio.gather(*tasks)

    assert call_count == 1, f"loader fired {call_count} times — lock not protecting cache fill"
    # All callers got the same snapshot object.
    first = snapshots[0]
    for snap in snapshots[1:]:
        assert snap is first


@pytest.mark.asyncio
async def test_invalidate_then_concurrent_reads_runs_loader_once(monkeypatch):
    """After invalidation, a fresh fanout must also coalesce — proves
    the lock isn't a one-shot ``threading.Lock``-equivalent that only
    works on the very first cache fill."""
    tenant_id = uuid.uuid4()

    call_count = 0
    release = asyncio.Event()

    async def fake_load_rows(db, tid):  # noqa: ANN001
        nonlocal call_count
        call_count += 1
        await release.wait()
        return {}

    monkeypatch.setattr(cfg_mod, "_load_rows", fake_load_rows)

    db = MagicMock()
    # Pre-warm the cache so the first call is a hit.
    cfg_mod._cache[tenant_id] = (1.0, MagicMock())

    cfg_mod.invalidate_tenant_cache(tenant_id)

    tasks = [asyncio.create_task(cfg_mod.get_tenant_integration_snapshot(db, tenant_id)) for _ in range(8)]
    for _ in range(5):
        await asyncio.sleep(0)

    release.set()
    await asyncio.gather(*tasks)

    assert call_count == 1, f"loader fired {call_count} times after invalidate — coalescing broken"


@pytest.mark.asyncio
async def test_different_tenants_dont_block_each_other(monkeypatch):
    """The lock must be per-tenant or fine-grained enough that tenant A's
    loader does not block tenant B's loader. A coarse global lock would
    serialize unrelated tenants and tank dispatch throughput."""
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()

    a_event = asyncio.Event()
    b_done = asyncio.Event()

    async def fake_load_rows(db, tid):  # noqa: ANN001
        if tid == tenant_a:
            # Park A's loader. If the lock is global, B's call below
            # cannot complete until A is released.
            await a_event.wait()
            return {}
        # Tenant B should be able to complete while A is parked.
        b_done.set()
        return {}

    monkeypatch.setattr(cfg_mod, "_load_rows", fake_load_rows)

    db = MagicMock()
    a_task = asyncio.create_task(cfg_mod.get_tenant_integration_snapshot(db, tenant_a))
    b_task = asyncio.create_task(cfg_mod.get_tenant_integration_snapshot(db, tenant_b))

    # B should finish without waiting on A.
    await asyncio.wait_for(b_done.wait(), timeout=2.0)
    await asyncio.wait_for(b_task, timeout=2.0)

    # Now release A and clean up.
    a_event.set()
    await asyncio.wait_for(a_task, timeout=2.0)


@pytest.mark.asyncio
async def test_use_cache_false_bypasses_lock_short_circuit(monkeypatch):
    """``use_cache=False`` must still load — and must still respect the
    in-flight lock so concurrent forced reloads don't pile up duplicate
    DB queries either."""
    tenant_id = uuid.uuid4()

    call_count = 0

    async def fake_load_rows(db, tid):  # noqa: ANN001
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0)  # cooperative yield
        return {}

    monkeypatch.setattr(cfg_mod, "_load_rows", fake_load_rows)

    # Pre-fill cache.
    cfg_mod._cache[tenant_id] = (1.0e9, MagicMock())  # very-future timestamp

    # use_cache=True → cache hit, no load.
    db = MagicMock()
    snap = await cfg_mod.get_tenant_integration_snapshot(db, tenant_id, use_cache=True)
    assert call_count == 0
    assert snap is cfg_mod._cache[tenant_id][1]

    # use_cache=False → forced reload.
    snap = await cfg_mod.get_tenant_integration_snapshot(db, tenant_id, use_cache=False)
    assert call_count == 1
