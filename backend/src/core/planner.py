"""Auto-decomposition planner — one founder message → many phase runs.

When the founder types "TODO 앱 만들어줘" they expect the company to
figure out design + backend + frontend + deploy on their own, not to
ping-pong four times. This module detects macro directions and asks
the tenant's LLM to produce a JSON phase plan, then seeds a linear
chain of ``ExecutionRun`` rows where each phase's ``parent_run_id``
points at the previous so the orchestrator's completion hook dispatches
them in order.

Conservative heuristic for "macro":
- Korean: contains ``앱`` / ``웹`` / ``사이트`` / ``서비스`` / ``풀스택`` etc.
  combined with an imperative verb (``만들`` / ``구현`` / ``제작``).
- English: contains ``app`` / ``website`` / ``service`` / ``fullstack``
  combined with ``build`` / ``make`` / ``create`` / ``implement``.

Non-macro requests skip planning entirely and run as a single phase,
same as today. That keeps short directions ("그 문단 바꿔줘") cheap.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

import litellm
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.models import ExecutionRun, ExecutorConfig, RunPriority, RunStatus

logger = structlog.get_logger(__name__)


_MACRO_SUBJECTS = re.compile(
    r"(?:"
    r"앱|웹|사이트|서비스|풀스택|프로젝트|시스템|플랫폼|API 서버|백엔드|프론트엔드|"
    r"\bapp\b|\bwebsite\b|\bweb\b|\bservice\b|\bfullstack\b|\bbackend\b|\bfrontend\b|\bsystem\b|\bplatform\b"
    r")",
    re.IGNORECASE,
)

_MACRO_VERBS = re.compile(
    r"(?:"
    r"만들|구현|제작|개발|설계|"
    r"\bbuild\b|\bmake\b|\bcreate\b|\bimplement\b|\bdevelop\b|\bdesign\b"
    r")",
    re.IGNORECASE,
)


def is_macro_direction(text: str) -> bool:
    """Best-effort: does this direction imply multiple phases?"""
    if not text or len(text) < 8:
        return False
    return bool(_MACRO_SUBJECTS.search(text)) and bool(_MACRO_VERBS.search(text))


_PLANNER_SYSTEM = (
    "You break a founder's software direction into 2–6 sequential phases. "
    "Each phase produces one concrete deliverable that a later phase can "
    "build on. Respond with STRICT JSON only (no prose, no markdown fences), "
    "matching exactly:\n"
    '{"phases": [{"name": "string, ≤24 chars", "direction": "string, a '
    "self-contained prompt for that phase (don't say 'above' or 'previously "
    "— restate what's needed)\"}, …]}\n"
    "Rules:\n"
    "- 2 phases for small tools; 3–4 for typical apps; up to 6 for complex "
    "ecosystems. Skip phases the founder clearly doesn't want.\n"
    "- Each ``direction`` is the full instruction for that phase and must "
    "mention any cross-phase contract (e.g. API endpoints, file names).\n"
    "- No phase should require humans outside the company (no 'hire a '\n"
    "  designer', no 'ask user for logo').\n"
    "- Match the language of the original direction."
)


@dataclass
class PhasePlan:
    name: str
    direction: str


async def maybe_plan_phases(
    *,
    direction: str,
    tenant_id: uuid.UUID,
    session: AsyncSession,
) -> list[PhasePlan] | None:
    """Return a phase list if the direction looks macro AND the tenant
    has an LLM-capable executor configured. Otherwise None — the caller
    runs a single phase.
    """
    if not is_macro_direction(direction):
        return None
    adapter_args = await _llm_adapter_args(session, tenant_id)
    if adapter_args is None:
        logger.info("planner_no_llm_executor_skipping", tenant_id=str(tenant_id))
        return None
    try:
        raw = await _run_planner_llm(direction, **adapter_args)
    except Exception as exc:  # noqa: BLE001 — planner mustn't break send_message
        logger.warning("planner_llm_failed", error=str(exc))
        return None
    phases = _parse_plan(raw)
    if not phases or len(phases) < 2:
        logger.info("planner_no_phases_or_trivial", phases=len(phases or []))
        return None
    return phases


async def _llm_adapter_args(
    session: AsyncSession, tenant_id: uuid.UUID
) -> dict[str, Any] | None:
    row = (
        await session.execute(
            select(ExecutorConfig).where(
                ExecutorConfig.tenant_id == tenant_id,
                ExecutorConfig.is_selected.is_(True),
            )
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


async def _run_planner_llm(
    direction: str,
    *,
    model: str,
    api_key: str,
    base_url: str | None,
) -> str:
    extra: dict[str, Any] = {}
    if model.startswith(("ollama/", "ollama_chat/")):
        extra["num_ctx"] = 40960
    resp = await litellm.acompletion(
        model=model,
        messages=[
            {"role": "system", "content": _PLANNER_SYSTEM},
            {"role": "user", "content": direction},
        ],
        api_key=api_key,
        api_base=base_url,
        max_tokens=4096,  # local reasoning models burn most of this on thinking
        temperature=0.2,
        timeout=600,
        **extra,
    )
    choice = resp.choices[0] if getattr(resp, "choices", None) else None
    msg = getattr(choice, "message", None) if choice else None
    content = getattr(msg, "content", None) if msg else None
    if content is None and isinstance(msg, dict):
        content = msg.get("content")
    return (content or "").strip()


def _parse_plan(raw: str) -> list[PhasePlan]:
    text = raw.strip()
    if text.startswith("```"):
        # Strip a leading code fence if the model ignored the rule.
        text = re.sub(r"^```[a-zA-Z0-9]*\n", "", text)
        text = re.sub(r"\n```\s*$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]+\}", text)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []
    raw_phases = data.get("phases") if isinstance(data, dict) else None
    if not isinstance(raw_phases, list):
        return []
    out: list[PhasePlan] = []
    for item in raw_phases:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()[:24]
        direction = str(item.get("direction") or "").strip()
        if not direction:
            continue
        out.append(PhasePlan(name=name or f"phase {len(out) + 1}", direction=direction))
    return out[:6]


async def seed_phase_chain(
    *,
    session: AsyncSession,
    root_run: ExecutionRun,
    phases: list[PhasePlan],
) -> list[ExecutionRun]:
    """Create one ExecutionRun per phase, linked as a blocked chain.

    ``root_run`` is the run dispatched by the initial send_message call.
    It gets rewritten to be phase 1 (takes on phase 1's directive).
    Phases 2..N are fresh ExecutionRuns seeded as ``blocked``; each
    ``parent_run_id`` points at the previous phase so the orchestrator
    chains them after each completion.
    """
    if not phases:
        return []

    # Phase 1 takes over the root run.
    first, *rest = phases
    root_run.directive = first.direction
    previous = root_run
    created: list[ExecutionRun] = [root_run]
    for p in rest:
        child = ExecutionRun(
            tenant_id=root_run.tenant_id,
            project_id=root_run.project_id,
            request_id=root_run.request_id,
            parent_run_id=previous.id,
            status=RunStatus.blocked,
            priority=RunPriority.medium,
            directive=p.direction,
        )
        session.add(child)
        await session.flush()
        created.append(child)
        previous = child
    logger.info(
        "planner_chain_seeded",
        run_id=str(root_run.id),
        phases=[p.name for p in phases],
    )
    return created
