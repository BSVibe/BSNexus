"""WorkerDispatchAdapter — publishes runs to a worker's Redis stream."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.src.core.worker_adapter import WorkerDispatchAdapter


@pytest.mark.asyncio
async def test_execute_publishes_to_worker_stream_and_returns_dispatched():
    stream_manager = MagicMock()
    stream_manager.publish = AsyncMock(return_value="stream-msg-1")

    worker_id = uuid.uuid4()
    run_id = uuid.uuid4()
    project_id = uuid.uuid4()

    adapter = WorkerDispatchAdapter(
        stream_manager=stream_manager,
        worker_id=worker_id,
        run_id=run_id,
        project_id=project_id,
    )

    out = await adapter.execute(
        "You are a helpful coding assistant. Implement X.",
        tools_allowed=["read", "write"],
    )

    stream_manager.publish.assert_awaited_once()
    stream_name, payload = stream_manager.publish.await_args.args
    assert stream_name == f"runs:worker:{worker_id}"
    assert payload["run_id"] == str(run_id)
    assert payload["project_id"] == str(project_id)
    assert payload["action"] == "execute"
    assert payload["system_prompt"].startswith("You are a helpful")
    assert "tools_allowed" in payload

    assert out["status"] == "dispatched"
    assert out["worker_id"] == str(worker_id)
    assert out["stream_msg_id"] == "stream-msg-1"
    # Critical: the dispatched sentinel tells the orchestrator NOT to
    # transition to done yet — the worker will finalize asynchronously.
    assert out["output_type"] is None
    assert out["output_ref"] is None


@pytest.mark.asyncio
async def test_execute_omits_tools_allowed_when_empty():
    stream_manager = MagicMock()
    stream_manager.publish = AsyncMock(return_value="m")
    adapter = WorkerDispatchAdapter(
        stream_manager=stream_manager,
        worker_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
    )

    await adapter.execute("hi", tools_allowed=[])

    _, payload = stream_manager.publish.await_args.args
    assert "tools_allowed" not in payload


def test_tools_supported_expanded_for_coding_cli_hint():
    # Consumed by RunOrchestrator._tools_from_executor_hint so the
    # template picker scores builder-persona runs higher for worker
    # executors (which run claude/codex/opencode locally).
    adapter = WorkerDispatchAdapter(
        stream_manager=MagicMock(),
        worker_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
    )
    for tool in ("read", "write", "exec", "git"):
        assert tool in adapter.tools_supported
