"""``decompose_request`` — single LLM call that turns a
:class:`ProjectContext` into a list of :class:`WorkStepDraft`.

Same model, same auth, same tool surface as the worker LLM (it just
runs with ``tools=None`` because this is reasoning, not execution).
The point is *not* to add a new role: it's the same work LLM doing its
first turn of thinking before the WorkStep loop starts.

Failure modes are silent and fall back to a single-step plan that
mirrors the Request intent. This is the same shape ``plan_and_dispatch_request``
used in G9, so a flaky decomposer call degrades the system to the
G9 behavior — never to a crash.
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog

from backend.src.core.executor_config.protocol import ExecutorClient
from backend.src.core.planning.context import ProjectContext
from backend.src.core.planning.prompts import render_decomposer_messages
from backend.src.core.work_steps import WorkStepDraft

logger = structlog.get_logger(__name__)


MAX_STEPS = 6
_PARSE_RETRIES = 1  # one retry on parse failure, then fall back
_WORK_STEP_NAME_MAX = 80


_FENCE_RE = re.compile(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", re.IGNORECASE)
_BARE_ARRAY_RE = re.compile(r"(\[[\s\S]*\])")


async def decompose_request(
    ctx: ProjectContext,
    *,
    executor: ExecutorClient,
    model: str,
    metadata: dict[str, Any] | None = None,
) -> list[WorkStepDraft]:
    """Return a list of WorkStepDrafts derived from ``ctx``.

    ``metadata`` is forwarded to ``executor.execute`` so the wire
    contracts of each path are satisfied (DirectLLMAdapter requires
    ``tenant_id`` + ``run_id``; BSGateway requires ``tenant_id``).
    Surfaced as a bug in the first prod dogfood: a missing-metadata
    call raised inside DirectLLMAdapter and silently triggered the
    single-step fallback. Production callers MUST pass both keys —
    a synthetic ``decompose:<request_id>`` works fine for ``run_id``
    since this LLM call doesn't have a tracked RunAttempt yet.

    Always returns at least one draft. If the LLM call fails, returns
    a single-step plan that mirrors the Request intent (G9 behavior).
    """
    intent = ctx.request_intent.strip()
    fallback = [_single_step_fallback(intent)]
    if not intent:
        return fallback

    messages = render_decomposer_messages(ctx, max_steps=MAX_STEPS)

    merged_metadata: dict[str, Any] = {"phase": "decompose", **(metadata or {})}

    text = await _call_with_parse_retry(executor=executor, model=model, messages=messages, metadata=merged_metadata)
    if text is None:
        logger.info("decompose_fallback", reason="llm_unavailable_or_unparseable", intent=intent[:80])
        return fallback

    drafts = _parse_drafts(text)
    if not drafts:
        logger.info("decompose_fallback", reason="no_valid_steps", intent=intent[:80])
        return fallback

    if len(drafts) > MAX_STEPS:
        drafts = _truncate_with_followup_marker(drafts)

    logger.info("decompose_succeeded", n_steps=len(drafts), intent=intent[:80])
    return drafts


async def _call_with_parse_retry(
    *,
    executor: ExecutorClient,
    model: str,
    messages: list[dict[str, str]],
    metadata: dict[str, Any],
) -> str | None:
    """Invoke the executor up to ``_PARSE_RETRIES + 1`` times. Returns
    the first text response whose JSON parses, or None if every attempt
    raises / returns blank / yields no valid JSON.
    """
    last_text: str | None = None
    for attempt in range(_PARSE_RETRIES + 1):
        try:
            result = await executor.execute(messages=messages, metadata=metadata, model=model, tools=None)
        except Exception as exc:  # noqa: BLE001 — decomposer must never bubble
            logger.warning("decompose_executor_error", attempt=attempt, error=str(exc))
            return None
        text = (result.get("output_ref") or "").strip()
        if not text:
            logger.warning("decompose_empty_output", attempt=attempt)
            continue
        last_text = text
        # If the text contains a parseable JSON array, return it; else
        # let the loop retry once more. The retry message is identical:
        # we're hoping the same prompt sampled differently produces
        # cleaner JSON.
        if _extract_json_array(text) is not None:
            return text
    return last_text  # may still be unparseable — caller falls back


def _extract_json_array(text: str) -> list[Any] | None:
    """Return the first JSON array found in ``text``, or None.

    Accepts: bare JSON array, ```json fenced``` block, or prose-then-JSON.
    """
    fenced = _FENCE_RE.search(text)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        match = _BARE_ARRAY_RE.search(text)
        candidate = match.group(1) if match else None
    if candidate is None:
        return None
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list):
        return None
    return parsed


def _parse_drafts(text: str) -> list[WorkStepDraft]:
    """Parse ``text`` into WorkStepDrafts. Invalid entries are dropped;
    a fully-invalid array returns ``[]`` and the caller falls back to
    the single-step plan."""
    raw = _extract_json_array(text)
    if raw is None:
        return []
    drafts: list[WorkStepDraft] = []
    for entry in raw:
        draft = _coerce_draft(entry)
        if draft is not None:
            drafts.append(draft)
    return drafts


def _coerce_draft(entry: Any) -> WorkStepDraft | None:
    if not isinstance(entry, dict):
        return None
    name = entry.get("name")
    objective = entry.get("objective")
    if not isinstance(name, str) or not name.strip():
        return None
    if not isinstance(objective, str) or not objective.strip():
        return None
    expected = entry.get("expected_outputs", [])
    if not isinstance(expected, list):
        expected = []
    expected_strs = [str(item).strip() for item in expected if str(item).strip()]
    return WorkStepDraft(
        name=name.strip()[:_WORK_STEP_NAME_MAX],
        objective=objective.strip(),
        expected_outputs=expected_strs,
    )


def _truncate_with_followup_marker(drafts: list[WorkStepDraft]) -> list[WorkStepDraft]:
    """Cap to ``MAX_STEPS`` and rename the last step so the founder
    can see the Request still has uncovered scope."""
    head = drafts[: MAX_STEPS - 1]
    last_original = drafts[MAX_STEPS - 1]
    last = WorkStepDraft(
        name=f"follow-up split required — {last_original.name}"[:_WORK_STEP_NAME_MAX],
        objective=last_original.objective,
        expected_outputs=last_original.expected_outputs,
    )
    return [*head, last]


def _single_step_fallback(intent: str) -> WorkStepDraft:
    """G9-equivalent single-step plan: Request title becomes the step
    name, full intent the objective."""
    name = (intent.splitlines()[0] if intent else "Request")[:_WORK_STEP_NAME_MAX] or "Request"
    return WorkStepDraft(name=name, objective=intent or name, expected_outputs=[])
