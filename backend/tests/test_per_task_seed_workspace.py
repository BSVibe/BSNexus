"""G6.9 — per-task seed workspace fixture spec change."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

from backend.src.core.domain import ProofState
from backend.src.quality.benchmark import (
    DEFAULT_M0_TASKS,
    BenchmarkTask,
    ScenarioKind,
    TaskKind,
)
from backend.src.quality.benchmark_bridge import BridgeConfig, _reset_workspace_to_seed, measure_task


@dataclass
class _StubExecutor:
    response_text: str = "Done."
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
        self.calls.append({"workspace_dir": workspace_dir, "tools": tools})
        if not any(message.get("role") == "tool" for message in messages):
            return {
                "output_type": "text",
                "output_ref": "",
                "actual_cost_cents": 0,
                "finish_reason": "tool_calls",
                "tool_calls": [
                    {
                        "id": f"call_seed_write_{len(self.calls)}",
                        "name": "file_write",
                        "arguments": {
                            "path": "src/generated_marker.py",
                            "content": "MARKER = 'ok'\n",
                        },
                    }
                ],
            }
        return {
            "output_type": "text",
            "output_ref": self.response_text,
            "actual_cost_cents": 0,
            "finish_reason": "stop",
            "tool_calls": None,
        }


def test_reset_workspace_wipes_existing_files_and_writes_seed(tmp_path):
    (tmp_path / "stale.py").write_text("old")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_stale.py").write_text("def test_stale(): pass")

    _reset_workspace_to_seed(
        tmp_path,
        {
            "pyproject.toml": "[project]\nname = 'seed'\n",
            "src/api.py": "def healthz(): return {'status': 'broken'}\n",
        },
    )

    assert not (tmp_path / "stale.py").exists()
    assert not (tmp_path / "tests" / "test_stale.py").exists()
    assert (tmp_path / "pyproject.toml").read_text().startswith("[project]")
    assert "healthz" in (tmp_path / "src" / "api.py").read_text()


def test_reset_workspace_preserves_ignored_cache_dirs(tmp_path):
    """``.git`` / ``__pycache__`` / ``.pytest_cache`` survive a reset
    so the verifier doesn't re-warm between tasks."""
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "warm.pyc").write_bytes(b"\x00")
    (tmp_path / "scratch.py").write_text("touched")

    _reset_workspace_to_seed(tmp_path, {"keep.py": "ok"})

    assert (tmp_path / ".git" / "HEAD").exists()
    assert (tmp_path / "__pycache__" / "warm.pyc").exists()
    assert not (tmp_path / "scratch.py").exists()
    assert (tmp_path / "keep.py").read_text() == "ok"


def test_reset_workspace_rejects_escape_paths(tmp_path):
    """Seed entries that try to write outside the workspace are silently
    skipped — the source of seed data is trusted but the writer keeps
    the invariant anyway."""
    _reset_workspace_to_seed(
        tmp_path,
        {
            "../escape.txt": "no",
            "/etc/passwd": "no",
            "ok.txt": "yes",
        },
    )
    assert (tmp_path / "ok.txt").read_text() == "yes"
    assert not (tmp_path.parent / "escape.txt").exists()


@pytest.mark.asyncio
async def test_measure_task_applies_seed_workspace_before_dispatch(
    db_session, test_session_maker, mock_tenant_id, seeded_tenant, tmp_path
):
    """End-to-end: when the task carries a seed_workspace, the bridge
    resets the tree before the LLM sees it. The executor's recorded
    workspace_dir contains the seed (asserted via filesystem read
    inside the test, since the stub doesn't introspect files)."""
    # Pre-populate the bridge workspace with junk that must get wiped.
    (tmp_path / "junk.py").write_text("stale")
    task = BenchmarkTask(
        id="seed-task",
        scenario=ScenarioKind.m0,
        kind=TaskKind.regression,
        title="Has fixture",
        prompt="Fix the broken function",
        expected_proof="python -m pytest",
        seed_workspace={
            "pyproject.toml": "[project]\nname = 'seed-task'\n",
            "src/api.py": "def f(): return 'broken'\n",
        },
    )
    config = BridgeConfig(
        tenant_id=mock_tenant_id,
        workspace_root=tmp_path,
        executor=_StubExecutor(),
        executor_kind="injected",
        model="stub",
        session_factory=test_session_maker,
    )

    await measure_task(task=task, config=config)

    # Bridge wiped junk, populated seed.
    assert not (tmp_path / "junk.py").exists()
    assert (tmp_path / "pyproject.toml").read_text().startswith("[project]")
    assert "broken" in (tmp_path / "src" / "api.py").read_text()


@pytest.mark.asyncio
async def test_measure_task_skips_reset_when_no_seed_workspace(
    db_session, test_session_maker, mock_tenant_id, seeded_tenant, tmp_path
):
    """Backward-compat: existing tasks without ``seed_workspace`` use
    whatever the bridge caller pre-populated."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='preexisting'\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_seed.py").write_text("def test_seed(): assert True\n")

    task = BenchmarkTask(
        id="no-seed",
        scenario=ScenarioKind.m0,
        kind=TaskKind.regression,
        title="No fixture",
        prompt="do something",
        expected_proof="python -m pytest",
    )
    config = BridgeConfig(
        tenant_id=mock_tenant_id,
        workspace_root=tmp_path,
        executor=_StubExecutor(),
        executor_kind="injected",
        model="stub",
        session_factory=test_session_maker,
    )
    telemetry = await measure_task(task=task, config=config)

    assert (tmp_path / "tests" / "test_seed.py").exists()
    assert telemetry.proof_state == ProofState.verified  # bare workspace still verifies


@pytest.mark.asyncio
async def test_measure_task_uses_default_seed_for_unfixtured_task(
    db_session, test_session_maker, mock_tenant_id, seeded_tenant, tmp_path
):
    (tmp_path / "leftover.py").write_text("stale")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "old.py").write_text("stale")

    task = BenchmarkTask(
        id="default-seed",
        scenario=ScenarioKind.easy,
        kind=TaskKind.test_writing,
        title="Default seed",
        prompt="do something",
        expected_proof="python -m pytest",
    )
    config = BridgeConfig(
        tenant_id=mock_tenant_id,
        workspace_root=tmp_path,
        executor=_StubExecutor(),
        executor_kind="injected",
        model="stub",
        session_factory=test_session_maker,
        default_seed_workspace={
            "pyproject.toml": "[project]\nname='baseline'\n",
            "tests/test_seed.py": "def test_seed(): assert True\n",
        },
    )

    await measure_task(task=task, config=config)

    assert not (tmp_path / "leftover.py").exists()
    assert not (tmp_path / "src" / "old.py").exists()
    assert (tmp_path / "pyproject.toml").read_text().startswith("[project]")


@pytest.mark.asyncio
async def test_measure_task_prefers_task_seed_over_default_seed(
    db_session, test_session_maker, mock_tenant_id, seeded_tenant, tmp_path
):
    task = BenchmarkTask(
        id="specific-seed",
        scenario=ScenarioKind.m0,
        kind=TaskKind.bug_fix,
        title="Specific seed",
        prompt="fix the task fixture",
        expected_proof="python -m pytest",
        seed_workspace={
            "pyproject.toml": "[project]\nname='specific'\n",
            "src/specific.py": "VALUE = 'specific'\n",
        },
    )
    config = BridgeConfig(
        tenant_id=mock_tenant_id,
        workspace_root=tmp_path,
        executor=_StubExecutor(),
        executor_kind="injected",
        model="stub",
        session_factory=test_session_maker,
        default_seed_workspace={"default_only.py": "should not appear"},
    )

    await measure_task(task=task, config=config)

    assert (tmp_path / "src" / "specific.py").exists()
    assert not (tmp_path / "default_only.py").exists()


def test_m0_1_has_seed_workspace_for_g6_9_research():
    """Pin: the m0-1 task ships with a concrete starter fixture. If
    this assertion fails after a future task-rewrite, decide explicitly
    whether to drop the fixture or update the test."""
    m0_1 = next(task for task in DEFAULT_M0_TASKS if task.id == "m0-1")
    assert m0_1.seed_workspace is not None
    assert "tests/test_healthz.py" in m0_1.seed_workspace
    assert "broken" in m0_1.seed_workspace["src/api.py"]
