"""Persist DirectLLMAdapter's in-memory ``tool_activity_log`` as
``ExecutionRunActivity`` rows.

Called from the dispatcher's Phase 3 (run finalization with a fresh
DB session) so the multi-minute LLM call doesn't hold a connection.
The adapter accumulates events in memory; this helper translates each
record into a row.

Mapped granularity:

- ``kind == "tool_call_start"`` / ``"tool_call_done"`` →
  ``ExecutionRunActivity(level=tool, event_type=<kind>)``
- ``kind == "llm_round_complete"`` (added by TASK-004) →
  ``ExecutionRunActivity(level=milestone, event_type="llm_round_complete")``

The detail JSON keeps every record field except ``kind`` (already
stored as ``event_type``) and ``occurred_at`` (used for ``created_at``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog

from backend.src.models import ExecutionRun, ExecutionRunActivity
from backend.src.models.execution_run_activity import ActivityLevel

logger = structlog.get_logger(__name__)


_MILESTONE_KINDS: frozenset[str] = frozenset({"llm_round_complete"})


def _build_summary(record: dict[str, Any]) -> str:
    """Short human-readable line for the Inside-panel timeline."""
    kind = record.get("kind", "")
    tool = record.get("tool_name") or ""
    round_idx = record.get("round_idx", "?")
    if kind == "tool_call_start":
        return f"[round {round_idx}] {tool}(…) start"
    if kind == "tool_call_done":
        outcome = record.get("outcome", "?")
        duration = record.get("duration_ms", 0)
        return f"[round {round_idx}] {tool} → {outcome} {duration}ms"
    if kind == "llm_round_complete":
        chars = record.get("content_chars", 0)
        n_calls = record.get("tool_call_count", 0)
        return f"[round {round_idx}] {chars} chars, {n_calls} tool calls"
    return f"[round {round_idx}] {kind}"


def _parse_occurred_at(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def build_activity_rows(
    run: ExecutionRun,
    activity_log: list[dict[str, Any]],
) -> list[ExecutionRunActivity]:
    """Translate an adapter's activity log into ORM rows.

    Pure function — no DB I/O, no commit. Caller adds + commits via
    its own session. Empty / missing log → empty list.
    """
    if not activity_log:
        return []

    rows: list[ExecutionRunActivity] = []
    for record in activity_log:
        if not isinstance(record, dict):
            continue
        kind = record.get("kind")
        if not isinstance(kind, str):
            continue

        level = ActivityLevel.milestone if kind in _MILESTONE_KINDS else ActivityLevel.tool
        detail = {k: v for k, v in record.items() if k not in {"kind", "occurred_at"}}
        row_kwargs: dict[str, Any] = {
            "run_id": run.id,
            "project_id": run.project_id,
            "level": level,
            "event_type": kind,
            "summary": _build_summary(record),
            "detail": detail,
        }
        occurred_at = _parse_occurred_at(record.get("occurred_at"))
        if occurred_at is not None:
            row_kwargs["created_at"] = occurred_at
        rows.append(ExecutionRunActivity(**row_kwargs))
    return rows


async def persist_tool_activity_log(
    run: ExecutionRun,
    activity_log: list[dict[str, Any]],
    session: Any,
) -> int:
    """Write activity rows for a finished run. Returns row count.

    Errors are logged and swallowed — instrumentation MUST NOT break
    the run-finalization path (per CLAUDE.md "never raise out of
    provider boundaries"). A missing activity log is the most common
    case we'll hit pre-rollout (BSGatewayAdapter doesn't populate
    one), so silently no-op there.
    """
    rows = build_activity_rows(run, activity_log)
    if not rows:
        return 0
    try:
        for row in rows:
            session.add(row)
        return len(rows)
    except Exception:  # noqa: BLE001
        logger.warning(
            "tool_activity_log_persist_failed",
            run_id=str(run.id),
            row_count=len(rows),
            exc_info=True,
        )
        return 0
