"""B — verification-feedback de-framing (`_format_aspect_feedback`).

A verification failure is either a code defect or an unrunnable
declared command. The old feedback always said "fix the workspace
files", so environment failures (OOM, ModuleNotFound) never converged.
The de-framed message presents the facts and lets the model decide.
"""

from __future__ import annotations

from types import SimpleNamespace

from backend.src.core.run_attempt_executor import _ASPECT_SUMMARY_CAP, _format_aspect_feedback


def _failure(*, aspect_type="declared_command", status="failed", exit_code=1, summary="boom"):
    return SimpleNamespace(
        aspect_type=SimpleNamespace(value=aspect_type),
        status=SimpleNamespace(value=status),
        exit_code=exit_code,
        summary=summary,
    )


def test_feedback_does_not_pre_judge_as_code_defect():
    msg = _format_aspect_feedback([_failure()], retries_left=3)
    # The old framing unconditionally blamed the code.
    assert "Fix the underlying issues in the workspace files" not in msg
    # The de-framed message offers both branches.
    assert "your CODE is wrong" in msg
    assert "verification COMMAND you declared cannot run" in msg


def test_feedback_translates_silent_oom_exit_137():
    msg = _format_aspect_feedback([_failure(exit_code=137, summary="")], retries_left=2)
    assert "exit 137" in msg
    assert "out of memory" in msg
    assert "NOT a code defect" in msg


def test_feedback_translates_command_not_found_exit_127():
    msg = _format_aspect_feedback([_failure(exit_code=127, summary="")], retries_left=2)
    assert "exit 127" in msg
    assert "not installed" in msg


def test_feedback_tail_truncates_long_output():
    head = "HEAD_MARKER " + ("x" * _ASPECT_SUMMARY_CAP)
    summary = head + " TAIL_MARKER"
    msg = _format_aspect_feedback([_failure(summary=summary)], retries_left=1)
    assert "TAIL_MARKER" in msg  # the cause, at the end, survives
    assert "HEAD_MARKER" not in msg  # the head is dropped
    assert "truncated" in msg


def test_feedback_handles_missing_summary():
    msg = _format_aspect_feedback([_failure(exit_code=1, summary=None)], retries_left=1)
    assert "(no output captured)" in msg


def test_feedback_includes_exit_code_and_retry_count():
    msg = _format_aspect_feedback([_failure(exit_code=5)], retries_left=4)
    assert "exit_code=5" in msg
    assert "4 aspect-retry round(s) left" in msg
