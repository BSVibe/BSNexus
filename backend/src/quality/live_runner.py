"""G6.4 — live M0 measurement CLI.

Usage::

    python -m backend.src.quality.live_runner \
        --tenant-id <uuid> \
        --workspace /path/to/seed \
        --output /tmp/m0_report.json

What it does:

  1. Connects to ``DATABASE_URL`` (same env var the backend uses).
  2. Resolves the per-tenant :class:`ExecutorClient` via
     :func:`resolve_executor` — typically returns either the
     BSGateway pool or the direct ``bsvibe_llm.LlmClient`` path
     (qwen3-coder:30b on a 48GB Mac Mini per the M0 spec).
  3. Walks ``default_tasks()`` through :func:`measure_task`.
  4. Evaluates the :class:`AcceptanceReport` and prints the markdown
     report to stdout. If ``--output`` is given, also writes the full
     JSON. Archive measurement output outside the tree (the repo
     gitignores ``backend/measurement/``); ``~/Docs/BSNexus/measurement/``
     is the convention.

This is the founder-runnable gate: ``greenfield_exit_ready == True``
in the JSON output is the green light for G8 repo-native delivery.
The CLI exits non-zero when the gate fails so a future cron can
guard on it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.src.config import settings as app_settings
from backend.src.core.executor_config.resolver import resolve_executor
from backend.src.models.executor_config import ExecutorConfig
from backend.src.quality.m0 import (
    BenchmarkTask,
    default_tasks,
    evaluate_task_result,
    evaluate_results,
    render_markdown_report,
)
from backend.src.quality.m0_executor import BridgeConfig, measure_task

logger = structlog.get_logger(__name__)


async def run_live_measurement(
    *,
    tenant_id: uuid.UUID,
    workspace_root: Path,
    database_url: str | None = None,
    tasks: list[BenchmarkTask] | None = None,
) -> dict:
    """Run all M0 tasks against the per-tenant executor config and
    return the :class:`AcceptanceReport.to_dict()` JSON payload."""
    url = database_url or app_settings.database_url
    engine = create_async_engine(url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        config_row = (
            await session.execute(select(ExecutorConfig).where(ExecutorConfig.tenant_id == tenant_id))
        ).scalar_one_or_none()
        if config_row is None:
            raise SystemExit(
                f"No ExecutorConfig row for tenant {tenant_id} — configure one via "
                "/api/v1/integrations before running the M0 gate."
            )
        executor = await resolve_executor(tenant_id=tenant_id, session=session)
        if executor is None:
            raise SystemExit(
                f"resolve_executor returned None for tenant {tenant_id} — check the "
                "executor config row's api_key + base_url."
            )
        executor_kind = config_row.kind.value
        model = config_row.model or ""

    bridge_config = BridgeConfig(
        tenant_id=tenant_id,
        workspace_root=workspace_root,
        executor=executor,
        executor_kind=executor_kind,
        model=model,
        session_factory=session_factory,
    )

    results = []
    for task in tasks or default_tasks():
        logger.info("m0_live_task_start", task=task.id, scenario=task.scenario.value)
        telemetry = await measure_task(task=task, config=bridge_config)
        result = evaluate_task_result(task, telemetry)
        results.append(result)
        logger.info(
            "m0_live_task_done",
            task=task.id,
            proof_state=telemetry.proof_state.value,
            strict_pass=result.strict_pass,
            failure_reason=result.failure_reason,
        )

    report = evaluate_results(results)
    await engine.dispose()
    return {
        "markdown": render_markdown_report(report),
        "report": report.to_dict(),
    }


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="m0-live", description="BSNexus M0 live measurement CLI")
    parser.add_argument("--tenant-id", required=True, help="Target tenant UUID (must have an ExecutorConfig row)")
    parser.add_argument(
        "--workspace",
        required=True,
        help="Path to the seed workspace the verifier runs against (must contain pyproject.toml or package.json)",
    )
    parser.add_argument(
        "--output",
        help="Optional path to write the full AcceptanceReport JSON (markdown still goes to stdout)",
    )
    parser.add_argument(
        "--database-url",
        help="Override DATABASE_URL (defaults to the backend's settings.database_url)",
    )
    return parser


async def _amain(argv: list[str]) -> int:
    parser = _build_argparser()
    args = parser.parse_args(argv)

    workspace_root = Path(args.workspace).resolve()
    if not workspace_root.exists():
        print(f"workspace not found: {workspace_root}", file=sys.stderr)
        return 2

    payload = await run_live_measurement(
        tenant_id=uuid.UUID(args.tenant_id),
        workspace_root=workspace_root,
        database_url=args.database_url,
    )
    print(payload["markdown"])

    if args.output:
        Path(args.output).write_text(json.dumps(payload["report"], indent=2, default=str))

    return 0 if payload["report"]["greenfield_exit_ready"] else 1


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_amain(argv if argv is not None else sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
