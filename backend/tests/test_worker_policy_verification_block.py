"""PR8 — pin the worker-shared-policy strengthened verification-block
instruction.

PR7 baseline collection on 2026-05-08 measured fenced-block emit rate
at 33% on qwen3-coder:30b. The PR8 prompt iteration strengthened the
instruction with NON-NEGOTIABLE language, a concrete clean-reply
example, anti-patterns (preamble after block, missing block, mismatched
command, claimed-but-not-emitted file_writes), and an explicit
fail-fast emission path so the chain finalizes deterministically.

This test pins the strengthened content so a future "cleanup"
refactor doesn't dilute the language and silently drop the emit
rate again.
"""

from __future__ import annotations

from backend.src.core.prompts.registry import load_prompt


def _policy_body() -> str:
    return load_prompt("worker-shared-policy")


def test_policy_marks_block_as_non_negotiable() -> None:
    body = _policy_body()
    assert "NON-NEGOTIABLE" in body, (
        "PR8 strengthened the block from 'should' to 'MUST' / 'NON-NEGOTIABLE'. "
        "If you removed this language, the prompt regressed back to PR6 wording "
        "and the local-LLM emit rate will drop below 50% again."
    )


def test_policy_includes_concrete_clean_reply_example() -> None:
    """Few-shot: a 'good' reply showing the block as the LAST content."""
    body = _policy_body()
    assert "Wrote `add.py`" in body
    assert "exit=0, 1 passed" in body
    # The example must reference a verification block right after the
    # prose so the LLM sees the structural pattern.
    example_idx = body.index("Wrote `add.py`")
    block_idx = body.index('"verifier_type": "software_test"', example_idx)
    assert block_idx > example_idx, "verification block must appear AFTER the prose in the example"


def test_policy_lists_anti_patterns_observed_in_dogfood() -> None:
    body = _policy_body()
    # Each anti-pattern from the PR7 baseline must be flagged.
    assert "block must be LAST" in body
    assert "No block at all" in body
    assert "doesn't match what shell_exec actually ran" in body
    assert "tool-call list before emitting the block" in body


def test_policy_documents_fail_fast_emission_path() -> None:
    """If the LLM cannot complete the task it MUST still emit the
    block (with a fail-fast command) so the chain finalizes at
    ``verification_failed`` instead of dead-ending at
    ``verification_missing``."""
    body = _policy_body()
    assert "EVEN IF you couldn't complete" in body
    assert '["false"]' in body, "fail-fast command example must be present"


def test_policy_documents_no_op_path_for_design_deliverables() -> None:
    """Pure design / docs deliverables (no automated check) may emit
    the block with ``["true"]`` rather than skip it."""
    body = _policy_body()
    assert '["true"]' in body
    assert "Pure design / docs deliverables" in body
