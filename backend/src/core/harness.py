"""Workspace context harness — refresh ``.bsnexus/context/*.md`` before every run.

Each phase in a planner-seeded chain runs in isolation; without a shared
view, phase 4 happily writes vanilla HTML files on top of phase 1's
Next.js scaffold. This module materialises the current project state
into markdown files in the workspace so subsequent phases can
``file_read`` them and stay coherent.

Pattern (ported from the agent-company-os main branch):

    .bsnexus/
    └── context/
        ├── workspace.md   # current files on disk (tree + sizes)
        ├── history.md     # prior phase summaries for this request
        └── stack.md       # tech-stack contract from the planner

``refresh_context`` rewrites workspace.md and history.md on every
dispatch; stack.md is written once by ``seed_phase_chain`` and then
read-only for the chain's lifetime.

The composer adds a pointer in the system prompt telling the worker to
``file_read .bsnexus/context/*.md`` before writing anything new.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core import project_workspace as workspace_store
from backend.src.models import ConversationMessage, ExecutionRun, Request, RunStatus

logger = structlog.get_logger(__name__)

HARNESS_DIR = ".bsnexus"
CONTEXT_DIR = "context"
STACK_FILE = "stack.md"
WORKSPACE_FILE = "workspace.md"
HISTORY_FILE = "history.md"

# Keep history compact — local models eat context fast. Take just the
# intent + a first-sentence summary of each prior phase.
MAX_HISTORY_ENTRIES = 10
MAX_HISTORY_SUMMARY_CHARS = 400


def harness_root(project_id: uuid.UUID) -> Path:
    """Return the ``.bsnexus/`` path inside a project workspace."""
    return workspace_store.project_workspace_path(project_id) / HARNESS_DIR


def context_dir(project_id: uuid.UUID) -> Path:
    path = harness_root(project_id) / CONTEXT_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_stack_contract(project_id: uuid.UUID, contract: str) -> None:
    """Persist the planner's stack contract as ``.bsnexus/context/stack.md``.

    Called once per chain from ``planner.seed_phase_chain``. Stable for the
    chain's lifetime; ``refresh_context`` never touches it.
    """
    if not contract or not contract.strip():
        return
    path = context_dir(project_id) / STACK_FILE
    path.write_text(_format_stack_md(contract.strip()), encoding="utf-8")


async def refresh_context(
    project_id: uuid.UUID,
    *,
    request: Request,
    db: AsyncSession,
) -> None:
    """Rewrite ``.bsnexus/context/{workspace,history}.md`` with fresh state.

    Called by the orchestrator right before composing a run's system
    prompt so each phase sees what the chain has produced so far.
    """
    ctx = context_dir(project_id)
    _write_workspace_md(project_id, ctx)
    await _write_history_md(ctx, request=request, db=db)


def _write_workspace_md(project_id: uuid.UUID, ctx: Path) -> None:
    # list_files hides ``.bsnexus/`` by default — exactly what we want:
    # the worker's view should match the founder's Files tab, not
    # include the harness plumbing itself.
    entries = workspace_store.list_files(project_id)
    path = ctx / WORKSPACE_FILE
    path.write_text(_format_workspace_md(entries), encoding="utf-8")


async def _write_history_md(ctx: Path, *, request: Request, db: AsyncSession) -> None:
    runs = list(
        (
            await db.execute(
                select(ExecutionRun)
                .where(
                    ExecutionRun.request_id == request.id,
                    ExecutionRun.status == RunStatus.done,
                )
                .order_by(ExecutionRun.completed_at.asc().nullslast())
            )
        ).scalars()
    )

    # Fetch the assistant ConversationMessage for each run so the
    # history reflects what the founder actually sees, not the raw
    # output_ref (which may be truncated or tool-log heavy).
    replies = await _load_assistant_replies(db, request_id=request.id)

    path = ctx / HISTORY_FILE
    path.write_text(
        _format_history_md(request=request, runs=runs, replies=replies),
        encoding="utf-8",
    )


async def _load_assistant_replies(db: AsyncSession, *, request_id: uuid.UUID) -> dict[uuid.UUID, str]:
    stmt = select(ConversationMessage).where(
        ConversationMessage.request_id == request_id,
        ConversationMessage.role == "assistant",
    )
    rows = list((await db.execute(stmt)).scalars())
    # Conversation messages aren't joined to runs directly; match by
    # chronological pairing — one assistant message per completed run,
    # in order.
    return {i: m.content for i, m in enumerate(rows)}  # type: ignore[return-value]


# ─────────────────────────── formatting ───────────────────────────


def _format_stack_md(contract: str) -> str:
    return (
        "# Stack Contract\n\n"
        "The planner committed this project to the following technical "
        "contract. Every phase worker MUST honor it — do NOT introduce a "
        "different runtime, framework, layout, or file convention.\n\n"
        f"{contract}\n"
    )


def _format_workspace_md(entries: list[dict]) -> str:
    if not entries:
        return "# Workspace\n\n(no files yet — this is the first phase. Stick to whatever stack.md declares.)\n"
    lines = [
        "# Workspace",
        "",
        "Files already present. Before creating a new file with the same "
        "purpose, `file_read` the existing one and extend / modify it "
        "rather than overwriting. Field names, route paths, schema shapes "
        "MUST match what's already on disk.",
        "",
        "```",
    ]
    for e in entries:
        lines.append(f"{e['path']:<60} {e['size']} B")
    lines.append("```")
    return "\n".join(lines) + "\n"


def _format_history_md(
    *,
    request: "Request",
    runs: list[ExecutionRun],
    replies: dict,
) -> str:
    lines = [
        "# History",
        "",
        f"Founder direction: {request.intent_summary}",
        "",
    ]
    if not runs:
        lines.append("(no prior phases — this is the first run.)")
        return "\n".join(lines) + "\n"

    lines.append("Prior phases in this chain (oldest first):")
    lines.append("")

    recent = runs[-MAX_HISTORY_ENTRIES:]
    for i, run in enumerate(recent, start=1):
        directive = (run.directive or "").strip().replace("\n", " ")
        summary = _first_paragraph(replies.get(i - 1, "") or _inline_of(run.output_ref))
        lines.append(f"## Phase {i}")
        lines.append(f"Directive: {directive[:240]}")
        lines.append(f"Outcome: {summary[:MAX_HISTORY_SUMMARY_CHARS]}")
        lines.append("")
    return "\n".join(lines) + "\n"


def _inline_of(output_ref: object) -> str:
    if not isinstance(output_ref, dict):
        return ""
    val = output_ref.get("inline")
    return val if isinstance(val, str) else ""


def _first_paragraph(text: str) -> str:
    if not text:
        return "(no summary)"
    cleaned = text.strip()
    # First blank-line-delimited paragraph.
    for chunk in cleaned.split("\n\n"):
        chunk = chunk.strip()
        if chunk:
            return chunk.replace("\n", " ")
    return cleaned.replace("\n", " ")
