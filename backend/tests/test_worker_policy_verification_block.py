"""Pin the worker-shared-policy verification-block essentials.

PR9 dogfood iter 4 surfaced that the bloated PR8 prompt
(``NON-NEGOTIABLE`` / ``MANDATORY ORDER`` / 6 anti-patterns / hard
rules / field rules / concrete example, ~90 lines) was actually
counterproductive on qwen3-coder:30b — the model burned attention on
rules and missed the core "do work, emit block" pattern.
PR9-fixup rewrote the policy lean (~30 lines).

These tests pin the load-bearing essentials so a future "let's add
more guidance!" PR can't bloat the prompt back to 90 lines without
flipping these assertions deliberately.
"""

from __future__ import annotations

from backend.src.core.prompts.registry import load_prompt


def _policy_body() -> str:
    return load_prompt("worker-shared-policy")


def test_policy_includes_verification_block_required_form() -> None:
    """The block format MUST be in the prompt — that's the protocol
    contract the parser keys off."""
    body = _policy_body()
    assert "bsnexus-verification" in body
    assert '"verifier_type"' in body
    assert '"command"' in body
    assert '"cwd"' in body
    assert '"timeout_s"' in body


def test_policy_documents_field_rules_for_block() -> None:
    body = _policy_body()
    assert "software_test" in body
    assert "software_build" in body
    assert "software_start" in body


def test_policy_documents_fail_fast_path() -> None:
    """If the LLM cannot complete the task it MUST still emit the
    block (with a fail-fast command) so the chain finalizes at
    ``verification_failed`` instead of dead-ending at
    ``verification_missing``."""
    body = _policy_body()
    assert '["false"]' in body


def test_policy_stays_lean() -> None:
    """PR9-fixup hard cap: keep the prompt concise. Earlier
    iterations bloated it past 80 lines and qwen3-coder:30b's
    medium-scenario reliability dropped. If a future PR needs to
    add guidance, prefer dropping less-effective text first.

    Threshold (50 lines) is a budget, not a hard physical limit;
    flipping this test means deliberately accepting the
    attention-budget tradeoff."""
    body = _policy_body()
    assert len(body.splitlines()) < 50, (
        f"prompt grew to {len(body.splitlines())} lines — see PR9 "
        "dogfood findings on local-LLM attention budget"
    )
