"""Pin the worker-shared-policy essentials.

PR10 — backend now derives the verification command from the run's
observed shell_exec history (PR9 dogfood proved the LLM-emit-block
approach hits a model compliance ceiling). The prompt no longer
mentions the bsnexus-verification fenced block at all; the LLM
just does its work naturally and the backend records what was run.

These tests pin the load-bearing essentials so the prompt can't
bloat back to 90 lines under future "let's add more guidance!"
PRs without flipping the assertions deliberately.
"""

from __future__ import annotations

from backend.src.core.prompts.registry import load_prompt


def _policy_body() -> str:
    return load_prompt("worker-shared-policy")


def test_policy_instructs_real_tool_use_not_pseudocode() -> None:
    body = _policy_body()
    assert "file_write" in body
    assert "shell_exec" in body
    assert "Real work, not pseudocode" in body


def test_policy_requires_verification_via_shell_exec() -> None:
    """The verification step is the LLM running shell_exec — backend
    records it as the deliverable's verification command. No marker
    emit required."""
    body = _policy_body()
    assert "verification command" in body.lower()
    assert "shell_exec" in body


def test_policy_no_longer_requires_fenced_block_emit() -> None:
    """PR10 — block emit instruction removed. The backend derives
    the block from the LLM's last successful shell_exec; this lifts
    the runtime-nudge ceiling we hit in PR8/PR9."""
    body = _policy_body()
    # Negative pin — these were the load-bearing-but-ineffective
    # phrases of PR8/PR9. Their absence is the PR10 architectural
    # commitment.
    assert "bsnexus-verification" not in body
    assert "fenced JSON block" not in body
    assert "verifier_type" not in body


def test_policy_stays_lean() -> None:
    """Hard cap. Earlier iterations bloated past 80 lines and
    qwen3-coder reliability dropped. PR10 reduces further to ~23
    lines by dropping the block-emit instruction entirely."""
    body = _policy_body()
    assert len(body.splitlines()) < 30, (
        f"prompt grew to {len(body.splitlines())} lines — see PR9/PR10 "
        "dogfood findings on local-LLM attention budget"
    )
