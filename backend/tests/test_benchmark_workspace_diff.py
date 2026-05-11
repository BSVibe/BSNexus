"""G6.5 — workspace-diff-aware ``fake_verified`` definition.

These tests pin down two invariants the 2026-05-11 first live run
exposed as too lax:

  1. When the verifier passes but the LLM didn't touch any file, the
     result must be ``fake_verified`` (not ``strict_pass``). Otherwise
     an always-passing pytest in the seed workspace masquerades as
     real work.
  2. The bridge measures the workspace diff *before* the verifier
     runs so verifier side effects (``__pycache__``, ``.pytest_cache``)
     don't inflate the count.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from backend.src.core.domain import ProofState
from backend.src.quality.benchmark import (
    BenchmarkTask,
    ScenarioKind,
    TaskKind,
    TaskTelemetry,
    evaluate_task_result,
)
from backend.src.quality.benchmark_bridge import BridgeConfig, measure_task


@dataclass
class _StubExecutor:
    response_text: str = "Done."
    file_writes: list[tuple[str, str]] = field(default_factory=list)
    workspace_root: Path | None = None
    calls: int = 0

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
        self.calls += 1
        if self.file_writes and self.calls == 1:
            return {
                "output_type": "text",
                "output_ref": "",
                "actual_cost_cents": 0,
                "finish_reason": "tool_calls",
                "tool_calls": [
                    {
                        "id": f"call_{idx}",
                        "name": "file_write",
                        "arguments": {"path": rel_path, "content": content},
                    }
                    for idx, (rel_path, content) in enumerate(self.file_writes)
                ],
            }
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


def _telemetry(**overrides: Any) -> TaskTelemetry:
    base = {
        "model": "m",
        "scenario_id": "x",
        "total_rounds": 0,
        "phase_rounds": {},
        "tool_count": 0,
        "repeated_tool_sequence_count": 0,
        "deliverables_created": 1,
        "proof_state": ProofState.verified,
        "verifier_command": ["python", "-m", "pytest"],
        "verifier_exit_code": 0,
        "decisions_created": 0,
        "terminal_reason": "summarized",
        "workspace_files_touched": 0,
    }
    base.update(overrides)
    return TaskTelemetry(**base)  # type: ignore[arg-type]


def test_fake_verified_is_true_when_verifier_passes_but_workspace_untouched():
    task = BenchmarkTask(
        id="t",
        scenario=ScenarioKind.m0,
        kind=TaskKind.bug_fix,
        title="t",
        prompt="t",
        expected_proof="test",
    )

    result = evaluate_task_result(task, _telemetry(workspace_files_touched=0))

    assert result.fake_verified is True
    assert result.strict_pass is False
    assert result.failure_reason == "fake_verified"


def test_fake_verified_is_false_when_verifier_passes_and_workspace_was_touched():
    task = BenchmarkTask(
        id="t",
        scenario=ScenarioKind.m0,
        kind=TaskKind.bug_fix,
        title="t",
        prompt="t",
        expected_proof="test",
    )

    result = evaluate_task_result(task, _telemetry(workspace_files_touched=2))

    assert result.fake_verified is False
    assert result.strict_pass is True


@pytest.mark.asyncio
async def test_bridge_reports_zero_files_touched_when_executor_does_not_write(
    db_engine, test_session_maker, mock_tenant_id, seeded_tenant, tmp_path
):
    _seed_python_workspace(tmp_path)
    task = BenchmarkTask(
        id="diff-1",
        scenario=ScenarioKind.easy,
        kind=TaskKind.test_writing,
        title="No file writes",
        prompt="Just talk.",
        expected_proof="python -m pytest",
    )
    executor = _StubExecutor(response_text="I would do the thing but did not.")
    config = BridgeConfig(
        tenant_id=mock_tenant_id,
        workspace_root=tmp_path,
        executor=executor,
        executor_kind="injected",
        model="stub-model",
        session_factory=test_session_maker,
    )

    telemetry = await measure_task(task=task, config=config)

    assert telemetry.workspace_files_touched == 0
    assert telemetry.proof_state == ProofState.verification_missing
    result = evaluate_task_result(task, telemetry)
    assert result.fake_verified is False
    assert result.failure_reason == "nonconvergent"


@pytest.mark.asyncio
async def test_bridge_reports_real_count_when_executor_writes_files(
    db_engine, test_session_maker, mock_tenant_id, seeded_tenant, tmp_path
):
    _seed_python_workspace(tmp_path)
    task = BenchmarkTask(
        id="diff-2",
        scenario=ScenarioKind.easy,
        kind=TaskKind.test_writing,
        title="Two file writes",
        prompt="Write a helper plus its test.",
        expected_proof="python -m pytest",
    )
    executor = _StubExecutor(
        workspace_root=tmp_path,
        file_writes=[
            ("src/helper.py", "def add(a, b):\n    return a + b\n"),
            ("tests/test_helper.py", "from src.helper import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"),
        ],
    )
    config = BridgeConfig(
        tenant_id=mock_tenant_id,
        workspace_root=tmp_path,
        executor=executor,
        executor_kind="injected",
        model="stub-model",
        session_factory=test_session_maker,
    )

    telemetry = await measure_task(task=task, config=config)

    assert telemetry.workspace_files_touched == 2
    result = evaluate_task_result(task, telemetry)
    assert result.fake_verified is False
    assert result.strict_pass is True


@pytest.mark.asyncio
async def test_bridge_ignores_pycache_and_pytest_cache_side_effects(
    db_engine, test_session_maker, mock_tenant_id, seeded_tenant, tmp_path
):
    """The bridge snapshots *before* the verifier runs so caches
    created by pytest don't count, but even with stale cache directories
    pre-existing the diff must stay 0 when no LLM writes happen.
    """
    _seed_python_workspace(tmp_path)
    # Simulate a previous pytest run's cache so the snapshot sees it.
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "stale.cpython-311.pyc").write_bytes(b"\x00\x01\x02")
    (tmp_path / ".pytest_cache").mkdir()
    (tmp_path / ".pytest_cache" / "lastfailed").write_text("{}\n")

    task = BenchmarkTask(
        id="diff-3",
        scenario=ScenarioKind.easy,
        kind=TaskKind.test_writing,
        title="Cache noise",
        prompt="Don't touch anything.",
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
    assert telemetry.workspace_files_touched == 0
