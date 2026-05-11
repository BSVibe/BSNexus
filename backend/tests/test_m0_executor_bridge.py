"""G6.4 — bridge tests for ``measure_task``.

These exercise the in-process pipeline that the live CLI in
``quality.live_runner`` re-uses with a real :class:`ExecutorClient`
and a real workspace on disk. The bridge is intentionally testable
without Redis or a long-running ``VerifierWorker`` because it calls
``process_one`` inline.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.src.core.domain import ProofState
from backend.src.quality.m0 import BenchmarkTask, ScenarioKind, TaskKind
from backend.src.quality.m0_executor import BridgeConfig, measure_task


@dataclass
class _StubExecutor:
    response_text: str = "Implemented per objective."
    raise_exc: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def execute(
        self,
        *,
        messages: list[dict[str, Any]],
        metadata: dict[str, Any],
        model: str,
        workspace_dir: str | None = None,
        mcp_servers: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        on_chunk: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        self.calls.append({"messages": messages, "metadata": metadata, "model": model, "tools": tools})
        if self.raise_exc is not None:
            raise self.raise_exc
        return {
            "output_type": "text",
            "output_ref": self.response_text,
            "actual_cost_cents": 0,
            "finish_reason": "stop",
            "tool_calls": None,
        }


def _seed_python_workspace(root: Path) -> None:
    (root / "pyproject.toml").write_text('[project]\nname = "m0-seed"\nversion = "0.0.0"\n')
    (root / "tests").mkdir()
    (root / "tests" / "test_seed.py").write_text("def test_seed():\n    assert True\n")


@pytest.mark.asyncio
async def test_measure_task_records_verifier_passing_python_workspace_as_verified(
    db_engine, test_session_maker, mock_tenant_id, seeded_tenant, tmp_path
):
    _seed_python_workspace(tmp_path)
    task = BenchmarkTask(
        id="bridge-1",
        scenario=ScenarioKind.easy,
        kind=TaskKind.test_writing,
        title="Bridge happy path",
        prompt="Add a small pure helper covered by pytest.",
        expected_proof="python -m pytest",
    )
    executor = _StubExecutor()
    config = BridgeConfig(
        tenant_id=mock_tenant_id,
        workspace_root=tmp_path,
        executor=executor,
        executor_kind="injected",
        model="stub-model",
        session_factory=test_session_maker,
    )

    telemetry = await measure_task(task=task, config=config)

    assert telemetry.scenario_id == "bridge-1"
    assert telemetry.model == "stub-model"
    assert telemetry.proof_state == ProofState.verified
    assert telemetry.verifier_exit_code == 0
    assert telemetry.verifier_command is not None
    assert "pytest" in " ".join(telemetry.verifier_command)
    assert telemetry.deliverables_created == 1
    assert telemetry.decisions_created == 0
    assert telemetry.terminal_reason == "summarized"
    assert len(executor.calls) == 1


@pytest.mark.asyncio
async def test_measure_task_records_no_workspace_files_as_human_review_required(
    db_engine, test_session_maker, mock_tenant_id, seeded_tenant, tmp_path
):
    """Workspace with no ``pyproject.toml`` / ``package.json`` → the
    proof selector returns ``None`` and stamps ``human_review_required``.
    """
    task = BenchmarkTask(
        id="bridge-2",
        scenario=ScenarioKind.smoke,
        kind=TaskKind.doc,
        title="No-policy workspace",
        prompt="Write a tiny note.",
        expected_proof="human_review_or_test",
        allow_human_review=True,
    )
    executor = _StubExecutor()
    config = BridgeConfig(
        tenant_id=mock_tenant_id,
        workspace_root=tmp_path,
        executor=executor,
        executor_kind="injected",
        model="stub-model",
        session_factory=test_session_maker,
    )

    telemetry = await measure_task(task=task, config=config)

    assert telemetry.proof_state == ProofState.human_review_required
    assert telemetry.verifier_command is None
    assert telemetry.deliverables_created == 1


@pytest.mark.asyncio
async def test_measure_task_records_executor_error_with_failed_terminal_reason(
    db_engine, test_session_maker, mock_tenant_id, seeded_tenant, tmp_path
):
    _seed_python_workspace(tmp_path)
    task = BenchmarkTask(
        id="bridge-3",
        scenario=ScenarioKind.medium,
        kind=TaskKind.bug_fix,
        title="Executor raises",
        prompt="Trigger an executor error.",
        expected_proof="test",
    )
    executor = _StubExecutor(raise_exc=RuntimeError("upstream 503"))
    config = BridgeConfig(
        tenant_id=mock_tenant_id,
        workspace_root=tmp_path,
        executor=executor,
        executor_kind="injected",
        model="stub-model",
        session_factory=test_session_maker,
    )

    telemetry = await measure_task(task=task, config=config)

    assert telemetry.proof_state == ProofState.verification_missing
    assert telemetry.deliverables_created == 0
    assert telemetry.verifier_command is None
    assert telemetry.terminal_reason.startswith("executor_error:")


@pytest.mark.asyncio
async def test_measure_task_isolates_each_task_under_its_own_session_factory(
    db_engine, mock_tenant_id, seeded_tenant, tmp_path
):
    """Each ``measure_task`` call walks through the bridge's own
    session_factory — proves the bridge doesn't lean on an outer
    session that would conflate state between tasks."""
    _seed_python_workspace(tmp_path)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    task = BenchmarkTask(
        id="bridge-4",
        scenario=ScenarioKind.easy,
        kind=TaskKind.test_writing,
        title="Isolated session",
        prompt="Add a small pure helper covered by pytest.",
        expected_proof="python -m pytest",
    )
    executor = _StubExecutor()
    config = BridgeConfig(
        tenant_id=mock_tenant_id,
        workspace_root=tmp_path,
        executor=executor,
        executor_kind="injected",
        model="stub-model",
        session_factory=factory,
    )

    first = await measure_task(task=task, config=config)
    second = await measure_task(task=task, config=config)

    assert first.proof_state == ProofState.verified
    assert second.proof_state == ProofState.verified
    # Two independent walks → two RunAttempt rows, not a shared one.
    assert first.scenario_id == second.scenario_id == "bridge-4"
    assert first.deliverables_created == 1
    assert second.deliverables_created == 1


@pytest.mark.asyncio
async def test_measure_task_increments_tool_event_count_when_executor_records_tools(
    db_engine, test_session_maker, mock_tenant_id, seeded_tenant, tmp_path
):
    """Telemetry's ``tool_count`` reflects ToolEvent rows from
    ``record_tool_event``. The minimal G6.3 dispatcher doesn't call
    tools yet, so the default count is zero — locking this in stops a
    future tool-loop change from silently breaking the metric."""
    _seed_python_workspace(tmp_path)
    task = BenchmarkTask(
        id="bridge-5",
        scenario=ScenarioKind.easy,
        kind=TaskKind.test_writing,
        title="No tool calls in G6.3 dispatcher",
        prompt="Add a small pure helper covered by pytest.",
        expected_proof="python -m pytest",
    )
    executor = _StubExecutor()
    config = BridgeConfig(
        tenant_id=mock_tenant_id,
        workspace_root=tmp_path,
        executor=executor,
        executor_kind="injected",
        model="stub-model",
        session_factory=test_session_maker,
    )

    telemetry = await measure_task(task=task, config=config)
    assert telemetry.tool_count == 0
    assert telemetry.repeated_tool_sequence_count == 0
