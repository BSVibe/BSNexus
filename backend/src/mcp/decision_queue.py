"""In-process registry that bridges ``MCP decision.wait`` to the
``POST /api/v1/decisions/{id}/resolve`` API.

When the BSGateway worker's claude CLI calls ``decision.wait`` over
MCP, the BSNexus tool implementation parks on this queue's per-decision
``asyncio.Event``. The decisions API's resolve handler calls
``notify(decision_id, result=...)`` which fires the Event, unblocking
the MCP call so claude resumes its own loop with the founder's choice.

Race-safe semantics:

- ``notify`` arriving before ``register`` (founder somehow resolves
  before the run starts) buffers the result; a subsequent ``register``
  + ``wait_for`` picks it up immediately. This handles the cold-start
  edge case where the dispatcher is still spinning up the BSGateway
  request.
- Multiple concurrent waiters on the same decision (uncommon — at most
  one CLI per run) all see the same broadcast result.
- ``wait_for`` raises ``DecisionWaitTimeout`` after ``timeout_seconds``
  so a long-pending decision doesn't hold the BSGateway connection
  past its 3600s budget.

This is a single-process mutable singleton; it does NOT survive a
restart. A restart in the middle of a blocking decision means the
worker will time out at its end and the Decision row stays open for
the founder to resolve later — when they do, the resolve API will
``notify`` an empty registry, which is a noop. The next dispatch of
that run will see the resolved Decision in the DB and skip the wait.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict
from typing import Any


class DecisionWaitTimeout(TimeoutError):
    """Raised when ``wait_for`` exceeds its timeout."""


# Buffered notifies (founder resolves before the BSGateway run reaches
# ``decision.wait``) are kept in ``_results`` until the matching
# register/wait_for picks them up. Without TTL the dict grows without
# bound across the lifetime of the process — every orphaned resolve
# (run timed out or got cancelled) leaks one entry. The cap is the
# run-total timeout from BSGateway (7200s) plus a small grace; older
# entries can't possibly be claimed by a still-live run.
_RESULT_TTL_SECONDS: float = 7800.0


class DecisionQueue:
    """Per-decision ``asyncio.Event`` registry."""

    def __init__(self, *, result_ttl_seconds: float = _RESULT_TTL_SECONDS) -> None:
        self._events: dict[uuid.UUID, asyncio.Event] = {}
        # ``defaultdict`` so a notify that arrives before register still
        # parks the result for the eventual register/wait_for pair.
        self._results: dict[uuid.UUID, dict[str, Any]] = {}
        self._results_ts: dict[uuid.UUID, float] = {}
        self._waiters: dict[uuid.UUID, int] = defaultdict(int)
        self._result_ttl_seconds = result_ttl_seconds

    def _prune_stale_results(self, *, now: float | None = None) -> None:
        """Drop buffered results past their TTL — runs that resolved them
        have already timed out and no waiter will ever claim them.

        Only removes entries that have *no* registered Event; an Event
        whose Run is still alive keeps the result regardless of age.
        """
        cutoff = (now if now is not None else time.monotonic()) - self._result_ttl_seconds
        stale = [
            decision_id
            for decision_id, ts in self._results_ts.items()
            if ts < cutoff and decision_id not in self._events
        ]
        for decision_id in stale:
            self._results.pop(decision_id, None)
            self._results_ts.pop(decision_id, None)

    def register(self, decision_id: uuid.UUID) -> None:
        """Create the Event for a decision so subsequent waiters can park.

        If a notify already buffered a result, the Event is created
        already-set so wait_for returns immediately."""
        self._prune_stale_results()
        if decision_id in self._events:
            return
        ev = asyncio.Event()
        if decision_id in self._results:
            ev.set()
        self._events[decision_id] = ev

    def notify(self, decision_id: uuid.UUID, *, result: dict[str, Any]) -> None:
        """Fire the Event with the founder's resolve payload.

        Buffered when no Event is registered yet — handled on the next
        ``register`` call. Stale buffered results pruned on every notify.
        """
        self._prune_stale_results()
        self._results[decision_id] = result
        self._results_ts[decision_id] = time.monotonic()
        ev = self._events.get(decision_id)
        if ev is not None:
            ev.set()

    async def wait_for(
        self,
        decision_id: uuid.UUID,
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        """Block until ``notify`` fires for this decision. Returns the
        founder's resolve payload (``{choice, notes, ...}``).

        Caller must have called ``register`` first; otherwise
        ``LookupError``.
        """
        ev = self._events.get(decision_id)
        if ev is None:
            raise LookupError(f"decision {decision_id} not registered")
        self._waiters[decision_id] += 1
        try:
            try:
                await asyncio.wait_for(ev.wait(), timeout=timeout_seconds)
            except TimeoutError as exc:  # asyncio.TimeoutError aliases TimeoutError on 3.11+
                raise DecisionWaitTimeout(
                    f"decision {decision_id} did not resolve within {timeout_seconds}s"
                ) from exc
            return self._results[decision_id]
        finally:
            self._waiters[decision_id] -= 1
            # Cleanup once the last waiter leaves AND the result was
            # delivered. Keeps the registry from growing without bound.
            if self._waiters[decision_id] <= 0 and ev.is_set():
                self._events.pop(decision_id, None)
                self._results.pop(decision_id, None)
                self._results_ts.pop(decision_id, None)
                self._waiters.pop(decision_id, None)


# Process-wide singleton — the resolve API and the MCP server both
# consume the same queue.
_queue = DecisionQueue()


def get_decision_queue() -> DecisionQueue:
    return _queue
