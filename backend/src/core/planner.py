"""Iterative replanner — agile-style next-step picker.

Replaces the legacy single-shot planner that locked the entire phase
chain at request time. The legacy approach was a waterfall: by the time
phase 4 ran, phase 1's findings couldn't influence phase 4's direction
because phase 4 was already seeded with its directive frozen.

The new model runs one LLM "chief-of-staff" pass at the start of every
iteration. Inputs:

- the founder's original request (``intent_summary``)
- ordered summaries of every prior run in this request (what was
  produced + what was learned)
- the latest founder messages on this conversation (so a
  modification mid-flight steers the next iteration)
- any open Decision rows the founder hasn't resolved

Output is one of:

- ``next_step`` → seed the next phase with a fresh directive
- ``done`` → goal is satisfied; close out
- ``ask_founder`` → there's a fork the company can't unilaterally pick;
  raise a Decision and stop until the founder resolves it

The same response always carries a ``founder_message`` — a short
human-language note that goes straight to the chat. That's how every
"design moment" surfaces to the founder without waiting for the phase
to finish executing.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Literal

import litellm
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.models import (
    ConversationMessage,
    Decision,
    ExecutionRun,
    ExecutorConfig,
    Request,
)

logger = structlog.get_logger(__name__)


# Decision codes the replanner returns. Keep stable — they're persisted
# in run.directive prefixes and surfaced to the frontend.
ReplanDecision = Literal["next_step", "done", "ask_founder"]


@dataclass
class ReplanResult:
    decision: ReplanDecision
    founder_message: str
    # Only when decision == "next_step":
    phase_name: str | None = None
    phase_direction: str | None = None
    # Only when decision == "ask_founder":
    question: str | None = None
    options: list[str] | None = None
    blocking: bool = True


_REPLANNER_SYSTEM = (
    "You are the chief-of-staff for an AI company the founder hired. The "
    "founder gives short directions; you keep the company moving by "
    "deciding the SINGLE next step at every iteration. This is agile — "
    "you can adapt based on what previous iterations actually produced.\n\n"
    "INPUTS you receive each turn:\n"
    "- ``intent``: the founder's original request, verbatim.\n"
    "- ``history``: ordered list of previously completed iterations, each "
    "with ``name``, ``directive`` they ran, ``summary`` (what the worker "
    "produced), and ``files_written`` (concrete file paths shipped that "
    "iteration). Read this CAREFULLY — picking a next_step that "
    "duplicates files already in ``files_written`` is the most common "
    "failure mode.\n"
    "- ``workspace_files``: every file currently on disk. Cross-reference "
    "this against your candidate next_step. If the files you'd ask the "
    "worker to create are ALL already in ``workspace_files``, you are "
    "either ``done`` or you need to pick a DIFFERENT next phase that "
    "EXTENDS the existing files (frontend, tests, deployment, etc.) — "
    "never re-scaffold the backend twice.\n"
    "- ``recent_messages``: latest founder turns in chat — pay attention "
    "to modifications like 'instead use X' or 'wait, also add Y'.\n"
    "- ``open_decisions``: questions you previously asked that are still "
    "unresolved. If any are blocking, do NOT pick next_step.\n\n"
    "OUTPUT: STRICT JSON, no prose, no markdown fences. Match exactly "
    "ONE of these three shapes:\n\n"
    "next_step (start the next iteration):\n"
    '{"decision":"next_step",'
    '"founder_message":"한국어/영어 1-3 sentences explaining what is '
    "starting and WHY this next, written to the founder, conversational "
    'tone","phase_name":"≤24 chars label",'
    '"phase_direction":"a self-contained worker prompt for that one '
    'iteration"}\n\n'
    "done (goal looks satisfied):\n"
    '{"decision":"done",'
    '"founder_message":"1-3 sentences summarizing what was '
    "shipped overall and inviting the founder to push further if they "
    'want"}\n\n'
    "ask_founder (you need a decision the founder must make):\n"
    '{"decision":"ask_founder",'
    '"founder_message":"1-3 sentences framing the fork in plain '
    'language",'
    '"question":"the actual question, ≤200 chars",'
    '"options":["short label A","short label B"],"blocking":true}\n\n'
    "Hard rules:\n"
    "1. Language: write founder_message in the SAME natural language "
    "the founder used in ``intent``. Don't translate. ``phase_name`` "
    "may stay short English.\n"
    "2. One step at a time: phase_direction must describe ONE iteration "
    "with ONE clear deliverable. No 'do A, then B, then C'.\n"
    "3. Self-containment: phase_direction must be a complete brief — "
    "restate file paths, framework, conventions. The worker won't see "
    "previous directives, only files on disk + history summaries.\n"
    "4. Adapt to what's there: if history shows the previous iteration "
    "produced X but failed at Y, the next phase_direction should "
    "ACKNOWLEDGE that and either fix Y or work around it. Don't repeat "
    "the same mistake.\n"
    "4b. NO DUPLICATE WORK. Before you commit to a phase_direction, "
    "list (mentally) what files it would create. Then check "
    "``workspace_files`` and the union of all ``history[].files_written`` "
    "lists. If your phase would mostly recreate paths that are already "
    "there (e.g. ``backend/package.json`` is in workspace and you're "
    "picking 'Setup Backend' — that's a duplicate), pivot to the NEXT "
    "natural step instead: write the frontend, add tests, wire up "
    "deployment, polish docs, run the verification command, etc. The "
    "directive must require NEW files or substantive edits, not "
    "rewrites of identical content.\n"
    "5. Finish: pick ``done`` as soon as the founder's intent is "
    "actually satisfied. Don't pad iterations — the founder hates busy "
    "work. If 3+ iterations have already shipped concrete files and the "
    "founder's intent is roughly covered, lean toward ``done``.\n"
    "6. Bail to founder: pick ``ask_founder`` only when the choice is "
    "genuinely a values/strategy call (auth provider, monetization "
    "model, etc.) — not for tactical defaults you can pick yourself.\n"
    "7. First iteration: when history is empty, decide what the very "
    "first concrete deliverable should be. For research-style "
    "intents, often the first step is 'gather and write up findings'. "
    "For build-style intents, often 'pick a stack and ship a minimal "
    "vertical slice'. Always pick something concrete, not 'plan the "
    "phases' or 'set up scaffolding'."
)


async def replan_next_step(
    *,
    request: Request,
    completed_runs: list[ExecutionRun],
    pending_decisions: list[Decision],
    recent_messages: list[ConversationMessage],
    tenant_id: uuid.UUID,
    session: AsyncSession,
) -> ReplanResult:
    """Ask the chief-of-staff LLM what to do next.

    Always returns a result — when no LLM is configured or the call
    fails, we fall back to a simple "treat the request like a single
    iteration" plan derived from ``request.intent_summary``.
    """
    blocking_unresolved = [d for d in pending_decisions if d.blocking and d.resolved_at is None]
    if blocking_unresolved:
        # Replanner can't decide forward while something is blocking.
        d = blocking_unresolved[0]
        return ReplanResult(
            decision="ask_founder",
            founder_message="아직 결정 대기 중인 항목이 있어요. 그것부터 답해주시면 이어서 진행할게요.",
            question=d.question,
            options=list(d.options) if isinstance(d.options, list) else None,
            blocking=True,
        )

    adapter_args = await _llm_adapter_args(session, tenant_id)
    if adapter_args is None:
        return _fallback_first_step(request, completed_runs)

    payload = _build_replanner_payload(request, completed_runs, recent_messages)
    try:
        raw = await _run_replanner_llm(payload, **adapter_args)
    except Exception as exc:  # noqa: BLE001 — replanner failure must never crash the loop
        logger.warning("replanner_llm_failed", error=str(exc))
        return _fallback_first_step(request, completed_runs)

    parsed = _parse_replan(raw)
    if parsed is None:
        logger.warning("replanner_unparseable_output", raw_preview=raw[:400])
        return _fallback_first_step(request, completed_runs)
    return parsed


def _fallback_first_step(request: Request, completed_runs: list[ExecutionRun]) -> ReplanResult:
    """Static fallback when the LLM isn't available.

    First iteration → run the founder's intent directly as the directive.
    Past first iteration with no LLM → declare done so we don't loop.
    """
    if completed_runs:
        return ReplanResult(
            decision="done",
            founder_message="여기까지 진행했어요. 추가로 더 할 일이 있으면 메시지 주세요.",
        )
    return ReplanResult(
        decision="next_step",
        founder_message=f"받았어요. 시작합니다: **{request.intent_summary[:120]}**",
        phase_name="iteration 1",
        phase_direction=request.intent_summary,
    )


def _build_replanner_payload(
    request: Request,
    completed_runs: list[ExecutionRun],
    recent_messages: list[ConversationMessage],
) -> dict[str, Any]:
    history: list[dict[str, Any]] = []
    for r in completed_runs:
        out = r.output_ref if isinstance(r.output_ref, dict) else {}
        files: list[str] = []
        for f in (out.get("files") or [])[:30]:
            if isinstance(f, dict) and f.get("path"):
                files.append(str(f["path"]))
        history.append(
            {
                "name": _name_from_directive(r.directive),
                "directive": (r.directive or "")[:600],
                "summary": str(out.get("founder_summary") or out.get("inline") or "")[:600],
                "files_written": files,
            }
        )

    recent: list[dict[str, str]] = []
    for m in recent_messages[-8:]:
        if m.role not in ("user", "assistant"):
            continue
        recent.append({"role": m.role, "content": (m.content or "")[:400]})

    # Snapshot of files actually on disk right now. Replanner sees this
    # alongside ``history[].files_written`` so it can detect when its
    # next pick would re-do something already shipped.
    workspace_files: list[str] = []
    try:
        from backend.src.core import workspace_store  # noqa: PLC0415

        for entry in workspace_store.list_files(request.project_id)[:200]:
            path = entry.get("path") if isinstance(entry, dict) else None
            if path:
                workspace_files.append(str(path))
    except Exception:  # noqa: BLE001 — replanner runs without it
        workspace_files = []

    return {
        "intent": request.intent_summary,
        "history": history,
        "workspace_files": workspace_files,
        "recent_messages": recent,
        "open_decisions": [],  # blocking decisions short-circuit before this point
    }


def _name_from_directive(directive: str | None) -> str:
    if not directive:
        return "iteration"
    first_line = directive.strip().splitlines()[0]
    return first_line[:24] or "iteration"


async def _llm_adapter_args(session: AsyncSession, tenant_id: uuid.UUID) -> dict[str, Any] | None:
    row = (
        await session.execute(
            select(ExecutorConfig)
            .where(
                ExecutorConfig.tenant_id == tenant_id,
                ExecutorConfig.is_selected.is_(True),
            )
            .order_by(ExecutorConfig.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    cfg = row.config or {}
    exec_type = (row.executor_type or "").lower()
    if exec_type == "generic_llm" and cfg.get("model"):
        return {
            "model": cfg["model"],
            "api_key": cfg.get("api_key") or "unused",
            "base_url": cfg.get("base_url"),
        }
    if exec_type == "bsgateway" and cfg.get("bsgateway_url"):
        return {
            "model": cfg.get("model") or "openai/gpt-4o-mini",
            "api_key": cfg.get("bsgateway_api_key") or "unused",
            "base_url": cfg["bsgateway_url"],
        }
    return None


async def _run_replanner_llm(
    payload: dict[str, Any],
    *,
    model: str,
    api_key: str,
    base_url: str | None,
) -> str:
    extra: dict[str, Any] = {}
    if model.startswith(("ollama/", "ollama_chat/")):
        extra["num_ctx"] = 32768
    resp = await litellm.acompletion(
        model=model,
        messages=[
            {"role": "system", "content": _REPLANNER_SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        api_key=api_key,
        api_base=base_url,
        max_tokens=4096,
        temperature=0.2,
        timeout=180,
        **extra,
    )
    choice = resp.choices[0] if getattr(resp, "choices", None) else None
    msg = getattr(choice, "message", None) if choice else None
    content = getattr(msg, "content", None) if msg else None
    if content is None and isinstance(msg, dict):
        content = msg.get("content")
    return (content or "").strip()


def _parse_replan(raw: str) -> ReplanResult | None:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9]*\n", "", text)
        text = re.sub(r"\n```\s*$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]+\}", text)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    decision = str(data.get("decision") or "").strip()
    founder_message = str(data.get("founder_message") or "").strip()
    if not founder_message:
        return None

    if decision == "next_step":
        direction = str(data.get("phase_direction") or "").strip()
        if not direction:
            return None
        return ReplanResult(
            decision="next_step",
            founder_message=founder_message,
            phase_name=str(data.get("phase_name") or "iteration").strip()[:24] or "iteration",
            phase_direction=direction,
        )

    if decision == "done":
        return ReplanResult(decision="done", founder_message=founder_message)

    if decision == "ask_founder":
        question = str(data.get("question") or "").strip()
        if not question:
            return None
        opts = data.get("options") or []
        options = [str(o).strip()[:120] for o in opts if isinstance(o, (str, int, float))]
        blocking = bool(data.get("blocking", True))
        return ReplanResult(
            decision="ask_founder",
            founder_message=founder_message,
            question=question[:240],
            options=options or None,
            blocking=blocking,
        )

    return None
