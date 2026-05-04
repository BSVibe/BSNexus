"""Tests for the ``run_output`` and ``decision_resolved`` SSE events
added by the Inside panel streaming path."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from backend.src.core.project_events import (
    ProjectEventBus,
    publish_decision_resolved,
    publish_run_output_chunk,
)


@pytest.mark.asyncio
async def test_run_output_event_shape() -> None:
    bus = ProjectEventBus()
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()

    # Subscribe first so the publish lands in the queue.
    received: list[dict] = []

    async def _consume() -> None:
        async for event in bus.subscribe(project_id):
            received.append(event)
            if event.get("finish_reason"):
                return

    consumer = asyncio.create_task(_consume())
    await asyncio.sleep(0)  # let subscribe register

    # Emit through the module-level helpers (singleton bus). For unit
    # isolation we call the underlying bus directly to avoid singleton
    # pollution across tests.
    await bus.publish(
        project_id,
        {"type": "run_output", "run_id": str(run_id), "content": "Hello ", "finish_reason": None},
    )
    await bus.publish(
        project_id,
        {"type": "run_output", "run_id": str(run_id), "content": "world", "finish_reason": None},
    )
    await bus.publish(
        project_id,
        {"type": "run_output", "run_id": str(run_id), "content": "", "finish_reason": "stop"},
    )

    await asyncio.wait_for(consumer, timeout=1.0)
    assert [r["content"] for r in received] == ["Hello ", "world", ""]
    assert received[-1]["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_publish_run_output_chunk_helper_uses_singleton() -> None:
    """Smoke-test the module helper actually emits onto the singleton."""
    from backend.src.core.project_events import get_project_event_bus

    project_id = uuid.uuid4()
    run_id = uuid.uuid4()

    received: list[dict] = []

    async def _consume() -> None:
        async for event in get_project_event_bus().subscribe(project_id):
            received.append(event)
            return

    consumer = asyncio.create_task(_consume())
    await asyncio.sleep(0)
    await publish_run_output_chunk(project_id, run_id=run_id, chunk="hi")
    await asyncio.wait_for(consumer, timeout=1.0)

    assert received[0]["type"] == "run_output"
    assert received[0]["content"] == "hi"
    assert received[0]["run_id"] == str(run_id)


@pytest.mark.asyncio
async def test_publish_decision_resolved_emits_event() -> None:
    from backend.src.core.project_events import get_project_event_bus

    project_id = uuid.uuid4()
    decision_id = uuid.uuid4()

    received: list[dict] = []

    async def _consume() -> None:
        async for event in get_project_event_bus().subscribe(project_id):
            received.append(event)
            return

    consumer = asyncio.create_task(_consume())
    await asyncio.sleep(0)
    await publish_decision_resolved(
        project_id, decision_id=decision_id, resolution="yes", resolved_by="founder@x"
    )
    await asyncio.wait_for(consumer, timeout=1.0)

    assert received[0]["type"] == "decision_resolved"
    assert received[0]["resolution"] == "yes"
    assert received[0]["resolved_by"] == "founder@x"
    assert received[0]["id"] == str(decision_id)
