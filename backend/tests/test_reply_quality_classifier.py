"""Pin: ``classify_reply(content, tool_call_count)`` returns one of
the five ``ReplyQualityKind`` values per the PR7 dogfood failure-mode
buckets.

The classifier is the empirical lens for PR8 prompt iteration: each
LLM round goes into exactly one bucket, and the dominant bucket per
run + per project tells us what's regressing or improving.
"""

from __future__ import annotations

from backend.src.core.llm.reply_quality import ReplyQualityKind, classify_reply


def test_real_tool_calls_when_tool_count_positive_and_no_pseudocode() -> None:
    out = classify_reply("Calling file_write now.", tool_call_count=2)
    assert out is ReplyQualityKind.real_tool_calls


def test_pseudocode_when_zero_tool_calls_and_chat_contains_tool_call_syntax() -> None:
    """LLM emitted Python-ish ``file_write(\"x.py\", \"...\")`` in chat
    instead of invoking the actual tool — most common failure mode for
    locally-hosted models that fall out of tool-calling mode mid-run."""
    out = classify_reply(
        'I\'ll do this:\n\nfile_write("add.py", "def add(a, b): return a + b")\n',
        tool_call_count=0,
    )
    assert out is ReplyQualityKind.pseudocode_in_chat


def test_pseudocode_recognises_shell_exec_pattern() -> None:
    out = classify_reply(
        "Now I'll run the tests:\n\nshell_exec('python -m pytest tests/')\n",
        tool_call_count=0,
    )
    assert out is ReplyQualityKind.pseudocode_in_chat


def test_pseudocode_recognises_file_read_pattern() -> None:
    out = classify_reply("First, file_read('add.py') to inspect.", tool_call_count=0)
    assert out is ReplyQualityKind.pseudocode_in_chat


def test_empty_when_zero_tool_calls_and_content_is_blank() -> None:
    assert classify_reply("", tool_call_count=0) is ReplyQualityKind.empty
    assert classify_reply("   \n  \t  ", tool_call_count=0) is ReplyQualityKind.empty


def test_fenced_block_only_when_content_is_just_verification_block() -> None:
    """Only the bsnexus-verification fence + nothing else → fenced_block_only.
    The LLM emitted the protocol marker but no narrative or tool calls.
    PR6 title-leak fix lives downstream, but the classifier flags it
    here so PR8 baseline shows how often this happens."""
    content = '```bsnexus-verification\n{"verifier_type": "software_test", "command": ["true"]}\n```\n'
    assert classify_reply(content, tool_call_count=0) is ReplyQualityKind.fenced_block_only


def test_mixed_when_tool_calls_present_AND_pseudocode_in_chat() -> None:
    """LLM tried both — invoked at least one real tool AND wrote
    pseudocode in chat. Useful signal: model has the right idea but
    doesn't fully commit to the tool surface."""
    content = "I'll start with file_read('add.py') first, then continue."
    assert classify_reply(content, tool_call_count=1) is ReplyQualityKind.mixed


def test_empty_when_chat_has_prose_but_zero_tool_calls_and_no_pseudocode() -> None:
    """The LLM said something coherent but did NOT do anything actionable
    — semantically empty in terms of work done. PR8 should target this."""
    content = "Sure, I'll get right on that. Working through the requirements..."
    assert classify_reply(content, tool_call_count=0) is ReplyQualityKind.empty


def test_pseudocode_pattern_inside_code_fence_still_counts() -> None:
    """LLM may put pseudocode inside ```python ... ``` fences. We
    still classify it as pseudocode_in_chat — the LLM is showing the
    tool-call as code-it-would-run instead of running it."""
    content = '```python\nfile_write("x.py", "code")\n```\n'
    assert classify_reply(content, tool_call_count=0) is ReplyQualityKind.pseudocode_in_chat


def test_real_tool_calls_when_word_file_write_appears_in_natural_prose() -> None:
    """Mention without parentheses is just commentary, not pseudocode.
    Don't flag this as pseudocode_in_chat."""
    content = "I just used file_write. The path is add.py."
    out = classify_reply(content, tool_call_count=1)
    assert out is ReplyQualityKind.real_tool_calls
