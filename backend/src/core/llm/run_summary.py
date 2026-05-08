"""Reduce a DirectLLMAdapter activity log + per-round reply text into
the ``RunSummary`` aggregate that ``ExecutionRun.run_summary`` stores.

Pure function over (activity_log, per_round_replies, final_reply_text).
The dispatcher constructs the inputs from data it already has — no DB
reads needed beyond the activity log it just wrote (or hasn't written
yet, since this runs before the activity persistence).

Outputs the JSON-friendly dict shape that mirrors the
``schemas/run_summary.py:RunSummary`` Pydantic model.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from backend.src.core.llm.reply_quality import ReplyQualityKind, classify_reply
from backend.src.core.verification_parser import _FENCE_RE


_FILE_WRITE_TOOL_NAMES: frozenset[str] = frozenset({"file_write"})


def _extract_file_path(args: Any) -> str | None:
    if not isinstance(args, str) or not args:
        return None
    try:
        parsed = json.loads(args)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    path = parsed.get("path")
    return path if isinstance(path, str) and path else None


def _files_actually_written(activity_log: list[dict[str, Any]]) -> list[str]:
    """Pair tool_call_start / tool_call_done by tool_call_id and emit
    paths from successful (``outcome="ok"``) ``file_write`` calls.
    Order preserved by start order. Duplicates collapsed."""
    starts_by_id: dict[str, dict[str, Any]] = {}
    paths_in_order: list[str] = []
    seen_paths: set[str] = set()
    for record in activity_log:
        if not isinstance(record, dict):
            continue
        kind = record.get("kind")
        if kind == "tool_call_start" and record.get("tool_name") in _FILE_WRITE_TOOL_NAMES:
            tcid = record.get("tool_call_id")
            if isinstance(tcid, str):
                starts_by_id[tcid] = record
        elif kind == "tool_call_done" and record.get("tool_name") in _FILE_WRITE_TOOL_NAMES:
            if record.get("outcome") != "ok":
                continue
            tcid = record.get("tool_call_id")
            start = starts_by_id.get(tcid) if isinstance(tcid, str) else None
            if start is None:
                continue
            path = _extract_file_path(start.get("args"))
            if path and path not in seen_paths:
                paths_in_order.append(path)
                seen_paths.add(path)
    return paths_in_order


def _round_records(activity_log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rounds: list[dict[str, Any]] = []
    for record in activity_log:
        if isinstance(record, dict) and record.get("kind") == "llm_round_complete":
            rounds.append(record)
    return rounds


def _classify_round(round_record: dict[str, Any], reply_text: str) -> ReplyQualityKind:
    return classify_reply(reply_text or "", int(round_record.get("tool_call_count") or 0))


def _dominant(per_round: list[dict[str, Any]]) -> ReplyQualityKind:
    if not per_round:
        return ReplyQualityKind.empty
    counter = Counter(r["reply_quality"] for r in per_round)
    most_common, _ = counter.most_common(1)[0]
    return ReplyQualityKind(most_common)


def compute_run_summary(
    *,
    activity_log: list[dict[str, Any]],
    per_round_replies: list[str],
    final_reply_text: str,
) -> dict[str, Any]:
    """Reduce raw activity into the ``RunSummary`` JSON dict.

    Args:
      activity_log — DirectLLMAdapter._tool_activity_log entries
        (mixed kinds: tool_call_start / tool_call_done /
        llm_round_complete).
      per_round_replies — concatenated content per LLM round, indexed
        by ``round_idx``. Used by the classifier; if shorter than
        the rounds list, missing rounds classify against empty text.
      final_reply_text — the run's final assistant reply
        (post-aggregate). Used to detect the bsnexus-verification
        fenced block.
    """
    rounds = _round_records(activity_log)
    per_round_payload: list[dict[str, Any]] = []
    total_tool_calls = 0
    for round_record in rounds:
        idx = int(round_record.get("round_idx") or 0)
        reply_text = per_round_replies[idx] if idx < len(per_round_replies) else ""
        kind = _classify_round(round_record, reply_text)
        tool_call_count = int(round_record.get("tool_call_count") or 0)
        total_tool_calls += tool_call_count
        per_round_payload.append(
            {
                "round_idx": idx,
                "content_chars": int(round_record.get("content_chars") or 0),
                "tool_call_count": tool_call_count,
                "reply_quality": kind.value,
                "finish_reason": round_record.get("finish_reason"),
            }
        )

    dominant = _dominant(per_round_payload)
    files_written = _files_actually_written(activity_log)
    # We measure "did the LLM emit the protocol marker" — distinct from
    # "did the metadata parse cleanly" (parse_verification_block enforces
    # the latter). For dashboard signal we want to count *attempts* so a
    # malformed block still flags the bucket.
    fenced_emitted = bool(_FENCE_RE.search(final_reply_text or ""))

    failure_signals: list[str] = []
    if fenced_emitted and not files_written:
        failure_signals.append(
            "Emitted bsnexus-verification fenced block but never invoked file_write — verifier will fail."
        )
    if rounds and total_tool_calls == 0:
        failure_signals.append("No tool calls dispatched across the entire run.")

    return {
        "total_rounds": len(rounds),
        "total_tool_calls": total_tool_calls,
        "per_round": per_round_payload,
        "dominant_reply_quality": dominant.value,
        "did_emit_fenced_block": fenced_emitted,
        "files_actually_written": files_written,
        "failure_signals": failure_signals,
    }
