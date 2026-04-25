"""WorkerResultConsumer — drains ``runs:results`` and finalises runs."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from backend.src.models import (
    ExecutionRun,
    Project,
    Request,
    RequestStatus,
    RunPriority,
    RunStatus,
)
from backend.src.queue.worker_result_consumer import (
    RESULTS_STREAM,
    WorkerResultConsumer,
)


async def _seed_running_run(db_session, tenant_id) -> ExecutionRun:
    project = Project(tenant_id=tenant_id, name="Test", description="")
    db_session.add(project)
    await db_session.flush()

    request = Request(
        tenant_id=tenant_id,
        project_id=project.id,
        intent_summary="Implement X",
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
    await db_session.commit()
    await db_session.refresh(run)
    return run


def _stream_manager():
    """A stream_manager with xgroup_create + consume + acknowledge + publish."""
    sm = MagicMock()
    sm.redis = MagicMock()
    sm.redis.xgroup_create = AsyncMock()
    sm.consume = AsyncMock()
    sm.acknowledge = AsyncMock()
    sm.publish = AsyncMock()
    sm.publish_project_event = AsyncMock()
    return sm


@pytest.mark.asyncio
async def test_success_message_marks_run_done(db_session, test_session_maker, mock_tenant_id, seeded_tenant):
    run = await _seed_running_run(db_session, mock_tenant_id)

    sm = _stream_manager()
    consumer = WorkerResultConsumer(stream_manager=sm, session_maker=test_session_maker, block_ms=10)
    await consumer._handle_message(
        {
            "run_id": str(run.id),
            "success": "true",
            "output_data": {"stdout": "ok"},
            "_message_id": "1-0",
        }
    )

    async with test_session_maker() as s:
        refreshed = (await s.execute(select(ExecutionRun).where(ExecutionRun.id == run.id))).scalar_one()
    assert refreshed.status == RunStatus.done
    assert refreshed.output_type == "text"
    assert refreshed.output_ref == {"stdout": "ok"}


@pytest.mark.asyncio
async def test_failure_message_marks_run_blocked_with_error(
    db_session, test_session_maker, mock_tenant_id, seeded_tenant
):
    run = await _seed_running_run(db_session, mock_tenant_id)

    sm = _stream_manager()
    consumer = WorkerResultConsumer(stream_manager=sm, session_maker=test_session_maker, block_ms=10)
    await consumer._handle_message(
        {
            "run_id": str(run.id),
            "success": "false",
            "error_message": "cli not found",
            "_message_id": "2-0",
        }
    )

    async with test_session_maker() as s:
        refreshed = (await s.execute(select(ExecutionRun).where(ExecutionRun.id == run.id))).scalar_one()
    assert refreshed.status == RunStatus.blocked
    assert refreshed.error_message == "cli not found"


@pytest.mark.asyncio
async def test_message_for_non_running_run_is_ignored(db_session, test_session_maker, mock_tenant_id, seeded_tenant):
    """Don't re-finalise a run that's already done or blocked."""
    run = await _seed_running_run(db_session, mock_tenant_id)
    run.status = RunStatus.done
    await db_session.commit()

    sm = _stream_manager()
    consumer = WorkerResultConsumer(stream_manager=sm, session_maker=test_session_maker, block_ms=10)
    # Should not raise; the state machine would block done→done.
    await consumer._handle_message(
        {
            "run_id": str(run.id),
            "success": "true",
            "_message_id": "3-0",
        }
    )

    async with test_session_maker() as s:
        refreshed = (await s.execute(select(ExecutionRun).where(ExecutionRun.id == run.id))).scalar_one()
    assert refreshed.status == RunStatus.done


@pytest.mark.asyncio
async def test_unknown_run_id_is_tolerated(db_session, test_session_maker, mock_tenant_id, seeded_tenant):
    sm = _stream_manager()
    consumer = WorkerResultConsumer(stream_manager=sm, session_maker=test_session_maker, block_ms=10)
    # No exception = tolerated.
    await consumer._handle_message(
        {
            "run_id": str(uuid.uuid4()),
            "success": "true",
            "_message_id": "4-0",
        }
    )


@pytest.mark.asyncio
async def test_malformed_message_is_tolerated(db_session, test_session_maker, mock_tenant_id, seeded_tenant):
    sm = _stream_manager()
    consumer = WorkerResultConsumer(stream_manager=sm, session_maker=test_session_maker, block_ms=10)
    await consumer._handle_message({"success": "true", "_message_id": "5-0"})
    await consumer._handle_message({"run_id": "not-a-uuid", "success": "true", "_message_id": "6-0"})


@pytest.mark.asyncio
async def test_run_loop_acknowledges_messages_after_processing(
    db_session, test_session_maker, mock_tenant_id, seeded_tenant
):
    run = await _seed_running_run(db_session, mock_tenant_id)

    sm = _stream_manager()
    # First consume yields the message; subsequent consumes return empty
    # so the loop can be cancelled.
    message = {
        "run_id": str(run.id),
        "success": "true",
        "output_data": {"stdout": "ok"},
        "_message_id": "7-0",
    }
    sm.consume.side_effect = [[message], [], [], []]

    consumer = WorkerResultConsumer(stream_manager=sm, session_maker=test_session_maker, block_ms=10)
    await consumer.start()

    # Give the loop a beat to consume + ack.
    for _ in range(20):
        await asyncio.sleep(0.02)
        if sm.acknowledge.await_count > 0:
            break
    await consumer.stop()

    sm.acknowledge.assert_awaited()
    stream_name, group, msg_id = sm.acknowledge.await_args.args
    assert stream_name == RESULTS_STREAM
    assert msg_id == "7-0"
