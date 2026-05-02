"""S1-3 — H12 ``ProjectEventBus._subs`` mutation needs an ``asyncio.Lock``.

The bus's subscriber dict is mutated from three coroutines:

  * ``subscribe`` — ``_subs.setdefault(project_id, set()).add(q)`` then
    ``_subs[project_id].discard(q)`` + ``_subs.pop(...)`` on cleanup.
  * ``publish`` — iterates ``_subs.get(project_id, ())`` to fan out.
  * concurrent ``subscribe`` calls for the same project also race the
    ``setdefault`` + ``add``.

Without a lock the ``RuntimeError: Set changed size during iteration``
in ``publish`` is possible when a subscriber unsubscribes mid-fan-out.
The fix wraps mutations in an ``asyncio.Lock`` and snapshots the
subscriber set before iterating.

These tests stress the bus with concurrent subscribe / unsubscribe /
publish to make the race observable without timing-fudge.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from backend.src.core.project_events import ProjectEventBus


@pytest.mark.asyncio
async def test_concurrent_subscribe_to_same_project_does_not_lose_queue():
    """Two coroutines subscribing to the same project must both end up
    in ``_subs[project_id]`` — a non-locked ``setdefault`` followed by
    ``add`` would lose one of them if the second coroutine read the
    old (still missing) entry."""
    bus = ProjectEventBus()
    project_id = uuid.uuid4()

    started = asyncio.Event()
    done = asyncio.Event()

    async def subscriber():
        gen = bus.subscribe(project_id)

        # Pull until done — the generator yields events from its queue.
        # We only need to enter and stay parked.
        async def run():
            async for _ in gen:
                pass

        task = asyncio.create_task(run())
        # Give it a tick to register.
        await asyncio.sleep(0)
        started.set()
        await done.wait()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, BaseException):
            pass

    sub1 = asyncio.create_task(subscriber())
    sub2 = asyncio.create_task(subscriber())

    # Wait for both to be running.
    for _ in range(20):
        await asyncio.sleep(0)

    # The bus should have a 2-element set for this project.
    queues = bus._subs.get(project_id, set())
    assert len(queues) == 2, f"expected 2 subscribers, got {len(queues)}"

    done.set()
    await asyncio.gather(sub1, sub2, return_exceptions=True)


@pytest.mark.asyncio
async def test_publish_during_unsubscribe_is_race_free():
    """A subscriber unsubscribing while ``publish`` is iterating must
    not raise ``RuntimeError: Set changed size during iteration``.

    Without locking, the test is timing-dependent; with the lock /
    snapshot, it's deterministic.
    """
    bus = ProjectEventBus()
    project_id = uuid.uuid4()

    # Spin up many subscribers.
    sub_tasks: list[asyncio.Task] = []
    sub_done = asyncio.Event()

    async def subscriber():
        gen = bus.subscribe(project_id)
        try:
            async for _ in gen:
                if sub_done.is_set():
                    return
        except asyncio.CancelledError:
            raise

    for _ in range(20):
        sub_tasks.append(asyncio.create_task(subscriber()))

    # Give them all a chance to register.
    for _ in range(20):
        await asyncio.sleep(0)

    # Now hammer publish + cancel concurrently.
    async def hammer_publish():
        for _ in range(50):
            try:
                await bus.publish(project_id, {"type": "tick"})
            except RuntimeError as exc:  # noqa: BLE001
                pytest.fail(f"publish raised {exc!r} — bus is not race-safe")
            await asyncio.sleep(0)

    publisher = asyncio.create_task(hammer_publish())

    # While publishing, cancel a subset of subscribers.
    for task in sub_tasks[::2]:
        task.cancel()

    await publisher
    sub_done.set()

    # Wait for everything to settle.
    for task in sub_tasks:
        if not task.done():
            task.cancel()
        try:
            await task
        except (asyncio.CancelledError, BaseException):
            pass


@pytest.mark.asyncio
async def test_publish_to_empty_project_is_noop_and_safe():
    bus = ProjectEventBus()
    # Should not raise even with zero subscribers.
    await bus.publish(uuid.uuid4(), {"type": "tick"})


@pytest.mark.asyncio
async def test_singleton_get_run_orchestrator_is_idempotent_under_concurrency():
    """``get_run_orchestrator()`` is a lazy ``_singleton`` init. Two
    concurrent first-callers must end up with the *same* orchestrator
    instance (TOCTOU on ``_singleton is None``).
    """
    from backend.src.core import run_orchestrator as ro_mod

    ro_mod._singleton = None

    results: list = []

    async def call():
        results.append(ro_mod.get_run_orchestrator())

    await asyncio.gather(*(call() for _ in range(20)))

    first = results[0]
    for r in results[1:]:
        assert r is first, "lazy singleton initialized more than once"


@pytest.mark.asyncio
async def test_singleton_get_project_event_bus_is_idempotent_under_concurrency():
    from backend.src.core import project_events as pe_mod

    pe_mod._singleton = None

    results: list = []

    async def call():
        results.append(pe_mod.get_project_event_bus())

    await asyncio.gather(*(call() for _ in range(20)))

    first = results[0]
    for r in results[1:]:
        assert r is first
