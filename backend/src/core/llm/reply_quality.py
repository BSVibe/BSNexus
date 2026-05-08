"""Classify each LLM round's chat reply into a fixed set of failure
modes. Used by the run_summary aggregator to give PR8's prompt
iteration an empirical baseline.

Five buckets, single-label per round:

- ``real_tool_calls`` — the LLM invoked tools and the chat narrative
  is plain prose (or empty). Healthy.
- ``pseudocode_in_chat`` — the LLM wrote ``file_write(...)`` /
  ``shell_exec(...)`` / etc. inside the chat instead of dispatching
  via the tool surface. Most common qwen3-coder failure mode in
  the 2026-05-08 dogfood.
- ``fenced_block_only`` — only the ``bsnexus-verification`` fence,
  no other narrative or tool calls. The LLM emitted the protocol
  marker but did nothing else.
- ``empty`` — zero tool calls AND content is blank / whitespace /
  generic acknowledgement with no actionable side-effect. The LLM
  said it would do something but didn't.
- ``mixed`` — at least one tool call AND pseudocode patterns in
  chat. The model tried both surfaces.

Pure function, zero deps on DB or ORM. Heuristics are conservative —
they err toward ``empty`` rather than flagging false positives in
``pseudocode_in_chat``, since the dashboard signal degrades fast if
``empty`` is overcounted but PR8 over-fitting kicks in fast if
``pseudocode_in_chat`` is over-flagged.
"""

from __future__ import annotations

import enum
import re

from backend.src.core.verification_parser import strip_verification_blocks


class ReplyQualityKind(str, enum.Enum):
    real_tool_calls = "real_tool_calls"
    pseudocode_in_chat = "pseudocode_in_chat"
    fenced_block_only = "fenced_block_only"
    empty = "empty"
    mixed = "mixed"


# Pseudocode signal: a known tool name immediately followed by ``(``,
# multi-line + case-insensitive so we catch both inline mentions and
# code-fenced blocks. Word boundary in front avoids matching innocuous
# substrings like ``profile_writer(`` or ``shell_executor(``.
_PSEUDOCODE_PATTERN = re.compile(
    r"\b(file_write|file_read|file_list|shell_exec|knowledge_search|decision_create|decision_wait)\s*\(",
    re.IGNORECASE | re.MULTILINE,
)


def _has_pseudocode(content: str) -> bool:
    return bool(_PSEUDOCODE_PATTERN.search(content))


def classify_reply(content: str, tool_call_count: int) -> ReplyQualityKind:
    """Single-label classification per round.

    ``content`` is the raw chat reply for the round (concatenation of
    streamed deltas before any verification-block stripping —
    classifier ignores the protocol marker via
    ``strip_verification_blocks`` for the empty/fenced split).
    ``tool_call_count`` is the number of tool calls dispatched THIS
    round.
    """
    text = content or ""
    has_pseudo = _has_pseudocode(text)

    if tool_call_count > 0:
        return ReplyQualityKind.mixed if has_pseudo else ReplyQualityKind.real_tool_calls

    if has_pseudo:
        return ReplyQualityKind.pseudocode_in_chat

    # Strip the ``bsnexus-verification`` fence so the empty/fenced
    # decision is made against narrative, not protocol metadata.
    stripped = strip_verification_blocks(text).strip()
    if not stripped:
        # Distinguish "fence + nothing" from "really empty" — the
        # former is a useful signal (LLM remembered the protocol but
        # forgot the work).
        if text.strip():
            return ReplyQualityKind.fenced_block_only
        return ReplyQualityKind.empty

    return ReplyQualityKind.empty
