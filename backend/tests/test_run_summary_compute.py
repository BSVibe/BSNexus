"""Pin: ``compute_run_summary`` reduces an adapter activity log + reply
text into the ``RunSummary`` aggregate that ``ExecutionRun.run_summary``
stores."""

from __future__ import annotations

from backend.src.core.llm.reply_quality import ReplyQualityKind
from backend.src.core.llm.run_summary import compute_run_summary


def _round(idx: int, content_chars: int = 0, tool_call_count: int = 0, finish_reason: str | None = "stop") -> dict:
    return {
        "kind": "llm_round_complete",
        "round_idx": idx,
        "content_chars": content_chars,
        "tool_call_count": tool_call_count,
        "finish_reason": finish_reason,
        "occurred_at": f"2026-05-08T15:0{idx}:00+00:00",
    }


def _tool_done(idx: int, name: str, outcome: str = "ok", path: str | None = None) -> list[dict]:
    args = "{}"
    if path is not None:
        args = '{"path": "' + path + '"}'
    return [
        {
            "kind": "tool_call_start",
            "round_idx": idx,
            "tool_name": name,
            "tool_call_id": f"c{idx}",
            "args": args,
            "occurred_at": f"2026-05-08T15:0{idx}:00+00:00",
        },
        {
            "kind": "tool_call_done",
            "round_idx": idx,
            "tool_name": name,
            "tool_call_id": f"c{idx}",
            "outcome": outcome,
            "duration_ms": 100,
            "occurred_at": f"2026-05-08T15:0{idx}:01+00:00",
        },
    ]


def test_summary_clean_run_three_rounds_real_tool_calls() -> None:
    log: list[dict] = []
    log.append(_round(0, content_chars=20, tool_call_count=1, finish_reason="tool_calls"))
    log.extend(_tool_done(0, "file_write", path="add.py"))
    log.append(_round(1, content_chars=20, tool_call_count=1, finish_reason="tool_calls"))
    log.extend(_tool_done(1, "file_write", path="tests/test_add.py"))
    log.append(_round(2, content_chars=120, tool_call_count=0, finish_reason="stop"))

    per_round_replies = ["calling tool", "calling tool", "Done. Tests pass."]
    summary = compute_run_summary(
        activity_log=log,
        per_round_replies=per_round_replies,
        final_reply_text='Done. Tests pass.\n\n```bsnexus-verification\n{"verifier_type": "software_test"}\n```',
    )
    assert summary["total_rounds"] == 3
    assert summary["total_tool_calls"] == 2
    assert summary["dominant_reply_quality"] == ReplyQualityKind.real_tool_calls.value
    assert summary["did_emit_fenced_block"] is True
    assert summary["files_actually_written"] == ["add.py", "tests/test_add.py"]
    assert len(summary["per_round"]) == 3
    assert summary["per_round"][0]["tool_call_count"] == 1


def test_summary_pseudocode_only_run_zero_tool_calls() -> None:
    log = [_round(0, content_chars=80, tool_call_count=0, finish_reason="stop")]
    per_round_replies = ['I will: file_write("add.py", "...")']
    summary = compute_run_summary(
        activity_log=log,
        per_round_replies=per_round_replies,
        final_reply_text=per_round_replies[0],
    )
    assert summary["total_tool_calls"] == 0
    assert summary["dominant_reply_quality"] == ReplyQualityKind.pseudocode_in_chat.value
    assert summary["did_emit_fenced_block"] is False
    assert summary["files_actually_written"] == []


def test_summary_mixed_run_picks_modal_kind() -> None:
    """Three rounds, two pseudocode + one real_tool_calls → pseudocode wins."""
    log: list[dict] = []
    log.append(_round(0, content_chars=20, tool_call_count=0))
    log.append(_round(1, content_chars=20, tool_call_count=1))
    log.extend(_tool_done(1, "file_read"))
    log.append(_round(2, content_chars=20, tool_call_count=0))
    summary = compute_run_summary(
        activity_log=log,
        per_round_replies=[
            'try file_write("a.py", "x")',
            "ok",
            'now shell_exec("ls")',
        ],
        final_reply_text='now shell_exec("ls")',
    )
    assert summary["dominant_reply_quality"] == ReplyQualityKind.pseudocode_in_chat.value
    # Total tool calls counted from llm_round_complete records.
    assert summary["total_tool_calls"] == 1


def test_summary_empty_run_no_rounds() -> None:
    """Adapter never logged a round (e.g. immediate connect failure)."""
    summary = compute_run_summary(activity_log=[], per_round_replies=[], final_reply_text="")
    assert summary["total_rounds"] == 0
    assert summary["total_tool_calls"] == 0
    assert summary["dominant_reply_quality"] == ReplyQualityKind.empty.value
    assert summary["per_round"] == []
    assert summary["files_actually_written"] == []


def test_summary_extracts_files_only_from_successful_file_writes() -> None:
    """A file_write that returned ``error`` outcome shouldn't show up
    in files_actually_written — workspace state is the source of truth
    but we approximate by trusting only ``ok`` outcomes."""
    log = [
        _round(0, content_chars=10, tool_call_count=2),
        *_tool_done(0, "file_write", outcome="ok", path="a.py"),
        *_tool_done(0, "file_write", outcome="error", path="b.py"),
    ]
    summary = compute_run_summary(
        activity_log=log,
        per_round_replies=["ok"],
        final_reply_text="ok",
    )
    assert summary["files_actually_written"] == ["a.py"]


def test_summary_handles_per_round_replies_shorter_than_rounds() -> None:
    """Defensive: if reply list is shorter, missing rounds get classified
    against empty content."""
    log = [_round(0), _round(1)]
    summary = compute_run_summary(
        activity_log=log,
        per_round_replies=["hi"],
        final_reply_text="hi",
    )
    assert len(summary["per_round"]) == 2
    # Round 0 has content "hi" (empty from classifier's POV — empty bucket),
    # round 1 has nothing.
    assert summary["per_round"][1]["reply_quality"] == ReplyQualityKind.empty.value


def test_summary_failure_signals_when_fenced_block_but_no_files() -> None:
    """Verifier-block-emitted but workspace empty → known failure mode
    (LLM claimed verification but never invoked file_write). We surface
    the diagnostic in failure_signals."""
    log = [_round(0, tool_call_count=0)]
    summary = compute_run_summary(
        activity_log=log,
        per_round_replies=["```bsnexus-verification\n{}\n```"],
        final_reply_text="```bsnexus-verification\n{}\n```",
    )
    assert summary["did_emit_fenced_block"] is True
    assert summary["files_actually_written"] == []
    assert any("fenced block" in s.lower() for s in summary["failure_signals"])
