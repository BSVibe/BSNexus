"""G6.4 — M0 harness bridge.

``measure_task`` runs a single :class:`BenchmarkTask` through the
production pipeline that landed in G6.1 / G6.2 / G6.3:

  BenchmarkTask
    → Project + Request + WorkPlan + WorkStep
    → ``dispatch_run_attempt`` (G6.3)
    → ``VerifierWorker.process_one`` (G6.1, called inline so we don't
       need Redis or the long-running consumer)
    → collect rows back into a :class:`TaskTelemetry`

The bridge is the only piece the M0 acceptance gate needs: once it
exists, ``run_quality_suite`` from ``quality.benchmark`` evaluates the
``AcceptanceReport`` (strict ≥ 7/10 + fake_verified == 0 for M0). The
live CLI in :mod:`backend.src.quality.live_runner` is a thin wrapper
that supplies a real :class:`ExecutorClient` from ``resolve_executor``
and a real ``workspace_root`` on disk.

Why call ``process_one`` inline instead of going through the queue:
during measurement we want deterministic ordering and no waiting on a
worker; the queue's only job (decouple HTTP latency from verifier
runtime) is irrelevant in batch mode.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.src.core.domain import ProofState, WorkPlanCreatedBy
from backend.src.core.executor_config.protocol import ExecutorClient
from backend.src.core.run_attempt_executor import dispatch_run_attempt
from backend.src.core.work_steps import WorkStepDraft, create_work_plan
from backend.src.models import (
    Decision,
    Deliverable,
    Project,
    Request,
    RunAttempt,
    ToolEvent,
    VerificationAspect,
    WorkStep,
)
from backend.src.core.domain import ProofAspectType
from backend.src.quality.benchmark import BenchmarkTask, TaskTelemetry
from backend.src.workers.verifier import process_one

logger = structlog.get_logger(__name__)


class _NoopStreamManager:
    """In-process stub for :class:`RedisStreamManager`.

    The bridge runs ``process_one`` inline so the
    ``proof:queue`` publish from ``dispatch_run_attempt`` is purely
    decorative during measurement. We swallow it instead of standing
    up a real Redis just to no-op against.
    """

    async def publish(self, stream: str, data: dict) -> str:  # noqa: ARG002 — duck-typing the publish surface
        return "noop"


class SessionFactory(Protocol):
    def __call__(self) -> AsyncSession: ...


@dataclass
class BridgeConfig:
    tenant_id: uuid.UUID
    workspace_root: Path
    executor: ExecutorClient
    executor_kind: str
    model: str
    session_factory: async_sessionmaker[AsyncSession]
    stream_manager: Any = None  # defaults to _NoopStreamManager()
    # Workspace baseline used when a task has no seed_workspace. Without
    # this, long live suites inherit files and failing tests from earlier
    # tasks, which makes later verifier failures non-local.
    default_seed_workspace: dict[str, str] | None = None

    def __post_init__(self) -> None:
        if self.stream_manager is None:
            object.__setattr__(self, "stream_manager", _NoopStreamManager())


async def measure_task(*, task: BenchmarkTask, config: BridgeConfig) -> TaskTelemetry:
    """End-to-end measurement for one :class:`BenchmarkTask`.

    Each step opens its own session so we never hold an open
    transaction across the LLM call or the verifier subprocess —
    matches how the dispatcher is wired in production.
    """
    project_id, request_id, work_step_id = await _seed_project_request_step(task=task, config=config)

    # G6.9/G6.11 — apply per-task seed fixture if defined; otherwise
    # fall back to the bridge's baseline so tasks do not inherit files
    # from the previous suite item.
    effective_seed = task.seed_workspace if task.seed_workspace is not None else config.default_seed_workspace
    if effective_seed is not None:
        _reset_workspace_to_seed(config.workspace_root, effective_seed)

    # G6.5 — snapshot the workspace *before* the LLM call so we can
    # tell whether the executor / future tool loop actually changed
    # files. Without this every always-passing pytest re-run shows up
    # as ``strict_pass`` even though the LLM didn't do anything.
    pre_snapshot = _snapshot_workspace(config.workspace_root)

    async with _session(config) as session:
        request = await session.get(Request, request_id)
        step = await session.get(WorkStep, work_step_id)
        assert request is not None and step is not None
        dispatch_result = await dispatch_run_attempt(
            request=request,
            work_step=step,
            tenant_id=config.tenant_id,
            session=session,
            stream_manager=config.stream_manager,
            executor=config.executor,
            executor_kind=config.executor_kind,
            model=config.model,
            workspace_dir=config.workspace_root,
        )
        attempt_id = dispatch_result.attempt.id
        deliverable_id = dispatch_result.deliverable.id if dispatch_result.deliverable else None

    # Compute the LLM-attributable workspace diff *before* the verifier
    # runs — otherwise side effects of ``pytest`` (``__pycache__``
    # bytecode, ``.pytest_cache``) would inflate the count. The ignore
    # list still catches them, but measuring pre-verifier makes the
    # signal precise instead of just "approximately correct".
    workspace_files_touched = _count_workspace_changes(config.workspace_root, pre_snapshot)

    if deliverable_id is not None:
        async with _session(config) as session:
            try:
                await process_one(
                    deliverable_id=deliverable_id,
                    tenant_id=config.tenant_id,
                    session=session,
                    publish_event=None,
                )
            except Exception as exc:
                logger.warning(
                    "m0_verifier_inline_failed",
                    task=task.id,
                    deliverable_id=str(deliverable_id),
                    error=str(exc),
                )

    return await _collect_telemetry(
        task=task,
        config=config,
        attempt_id=attempt_id,
        deliverable_id=deliverable_id,
        request_id=request_id,
        project_id=project_id,
        workspace_files_touched=workspace_files_touched,
    )


async def _seed_project_request_step(
    *, task: BenchmarkTask, config: BridgeConfig
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    async with _session(config) as session:
        project = Project(
            tenant_id=config.tenant_id,
            name=task.title,
            description=task.prompt,
            workspace_dir=str(config.workspace_root),
        )
        session.add(project)
        await session.flush()

        request = Request(
            tenant_id=config.tenant_id,
            project_id=project.id,
            intent=task.prompt,
        )
        session.add(request)
        await session.commit()
        await session.refresh(request)

        plan = await create_work_plan(
            request=request,
            steps=[
                WorkStepDraft(
                    name=task.title,
                    objective=task.prompt,
                    expected_outputs=[task.expected_proof] if task.expected_proof else [],
                ),
            ],
            created_by=WorkPlanCreatedBy.system,
            session=session,
        )
        step = (await session.execute(select(WorkStep).where(WorkStep.plan_id == plan.id))).scalar_one()
        return project.id, request.id, step.id


async def _collect_telemetry(
    *,
    task: BenchmarkTask,
    config: BridgeConfig,
    attempt_id: uuid.UUID,
    deliverable_id: uuid.UUID | None,
    request_id: uuid.UUID,
    project_id: uuid.UUID,  # noqa: ARG001 — kept for future per-project telemetry (cost / cache hits)
    workspace_files_touched: int = 0,
) -> TaskTelemetry:
    async with _session(config) as session:
        attempt = await session.get(RunAttempt, attempt_id)
        assert attempt is not None

        tool_events = (
            (
                await session.execute(
                    select(ToolEvent).where(ToolEvent.run_attempt_id == attempt_id).order_by(ToolEvent.round_index)
                )
            )
            .scalars()
            .all()
        )

        deliverables_count = (
            await session.execute(select(func.count(Deliverable.id)).where(Deliverable.request_id == request_id))
        ).scalar_one()

        decisions_count = (
            await session.execute(select(func.count(Decision.id)).where(Decision.request_id == request_id))
        ).scalar_one()

        deliverable = None
        latest_test_aspect: VerificationAspect | None = None
        if deliverable_id is not None:
            deliverable = await session.get(Deliverable, deliverable_id)
            latest_test_aspect = (
                await session.execute(
                    select(VerificationAspect)
                    .where(
                        VerificationAspect.deliverable_id == deliverable_id,
                        VerificationAspect.aspect_type == ProofAspectType.code_test,
                    )
                    .order_by(VerificationAspect.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

    proof_state = deliverable.proof_state if deliverable is not None else ProofState.verification_missing
    verifier_command, verifier_exit_code = _extract_verifier_signal(latest_test_aspect)
    phase_rounds = (attempt.telemetry or {}).get("phase_rounds") or {}

    return TaskTelemetry(
        model=attempt.model or config.model,
        scenario_id=task.id,
        total_rounds=attempt.round_count,
        phase_rounds={str(k): int(v) for k, v in phase_rounds.items()},
        tool_count=len(tool_events),
        repeated_tool_sequence_count=_count_repeated_sequences(tool_events),
        deliverables_created=int(deliverables_count or 0),
        proof_state=proof_state,
        verifier_command=verifier_command,
        verifier_exit_code=verifier_exit_code,
        decisions_created=int(decisions_count or 0),
        terminal_reason=attempt.terminal_reason or "",
        workspace_files_touched=workspace_files_touched,
    )


def _extract_verifier_signal(
    aspect: VerificationAspect | None,
) -> tuple[list[str] | None, int | None]:
    if aspect is None:
        return None, None
    inputs = aspect.inputs or {}
    commands = inputs.get("commands") if isinstance(inputs, dict) else None
    if isinstance(commands, list) and commands:
        first = commands[0]
        if isinstance(first, list):
            return [str(part) for part in first], aspect.exit_code
    return None, aspect.exit_code


def _count_repeated_sequences(events: list[ToolEvent]) -> int:
    """Number of times a (tool_name, args_hash) pair repeated the
    previous round — matches the repetition-nudge bookkeeping in
    ``run_attempts.record_tool_event``.
    """
    count = 0
    for previous, current in zip(events, events[1:], strict=False):
        if previous.tool_name == current.tool_name and previous.args_hash == current.args_hash:
            count += 1
    return count


@asynccontextmanager
async def _session(config: BridgeConfig) -> AsyncIterator[AsyncSession]:
    async with config.session_factory() as session:
        yield session


# G6.5 — workspace diff measurement.
# Ignore set covers VCS metadata + build/test caches that get touched as
# a side effect of running the verifier itself. They would inflate the
# touched-files count without representing LLM work.
_WORKSPACE_IGNORE_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "venv",
        "node_modules",
        ".next",
        "dist",
        "build",
        ".turbo",
    }
)
_WORKSPACE_IGNORE_SUFFIXES: tuple[str, ...] = (
    ".pyc",
    ".pyo",
    ".log",
)


def _snapshot_workspace(root: Path) -> dict[str, tuple[float, int]]:
    """Map of ``relative_path → (mtime, size)`` for every non-ignored
    file under ``root``. Returns an empty dict when ``root`` doesn't
    exist (callers tolerate it).
    """
    if not root.exists():
        return {}
    snapshot: dict[str, tuple[float, int]] = {}
    for path in root.rglob("*"):
        if _is_ignored(path, root):
            continue
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        snapshot[str(path.relative_to(root))] = (stat.st_mtime, stat.st_size)
    return snapshot


def _count_workspace_changes(root: Path, baseline: dict[str, tuple[float, int]]) -> int:
    """Count files that were added/removed/modified vs ``baseline``."""
    current = _snapshot_workspace(root)
    changed = 0
    for rel_path, current_meta in current.items():
        baseline_meta = baseline.get(rel_path)
        if baseline_meta is None or baseline_meta != current_meta:
            changed += 1
    for rel_path in baseline:
        if rel_path not in current:
            changed += 1
    return changed


def _is_ignored(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    if any(part in _WORKSPACE_IGNORE_DIRS for part in relative.parts):
        return True
    if path.is_file() and path.suffix in _WORKSPACE_IGNORE_SUFFIXES:
        return True
    return False


def _reset_workspace_to_seed(root: Path, seed: dict[str, str]) -> None:
    """G6.9 — wipe the workspace's non-ignored files and write the
    task's seed fixture. Cache dirs (``.git``, ``__pycache__``,
    ``.pytest_cache`` …) are preserved so the verifier doesn't have to
    re-warm them between tasks. Paths inside ``seed`` are taken as
    workspace-relative; absolute or ``..`` paths are rejected.
    """
    root = root.resolve()
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)
    for path in list(root.rglob("*")):
        if _is_ignored(path, root):
            continue
        if path.is_file() or path.is_symlink():
            try:
                path.unlink()
            except OSError:
                pass
    # Remove emptied directories bottom-up, skipping the root + ignore set.
    for path in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True):
        if _is_ignored(path, root):
            continue
        try:
            path.rmdir()
        except OSError:
            pass
    for relative_path, content in seed.items():
        if not isinstance(relative_path, str) or not relative_path:
            continue
        if relative_path.startswith("/") or ".." in Path(relative_path).parts:
            continue
        target = (root / relative_path).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
