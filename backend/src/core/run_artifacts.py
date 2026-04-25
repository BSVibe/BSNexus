"""Post-run side effects — surface a completed run's output in the UI.

``ExecutionRun.output_ref`` alone isn't visible to the founder; two
things need to be materialised after a run transitions to ``done``:

1. An ``assistant``-role ``ConversationMessage`` on the originating
   request so the Direction chat gets an actual reply.
2. A ``Deliverable`` + ``DeliverableVersion`` row so the Progress tab
   surfaces the output as a proper artefact.

Files themselves are already on disk — workers write them via the
``file_write`` tool during the run; this module only records metadata.
Both writes are idempotent: re-running on the same completed run is a
no-op.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.models import (
    ConversationMessage,
    Deliverable,
    DeliverableStatus,
    DeliverableType,
    DeliverableVersion,
    Project,
    Request,
    RunStatus,
    StorageBackend,
)

if TYPE_CHECKING:
    from backend.src.core.composer import KnowledgeClient
    from backend.src.models import ExecutionRun

logger = structlog.get_logger(__name__)


async def publish_run_output(
    run: "ExecutionRun",
    session: AsyncSession,
    *,
    knowledge: "KnowledgeClient | None" = None,
) -> None:
    """Materialise a completed run's chat reply + deliverable in the UI.

    When ``knowledge`` is provided, the deliverable is also indexed back
    into BSage so future projects can find it via search. Indexing
    failures are logged but never raised — deliverable creation always
    succeeds regardless of BSage health.
    """
    if run.status != RunStatus.done:
        return
    summary = _extract_founder_summary(run.output_ref)
    inline = _extract_inline(run.output_ref)
    files = _extract_files(run.output_ref)

    if not summary and not inline and not files:
        logger.info("publish_run_output_empty", run_id=str(run.id))
        return

    reply_text = summary or inline or _default_summary(files)
    await _ensure_assistant_message(run, reply_text, session)
    deliverable = await _ensure_deliverable(run, reply_text, files, session)

    if knowledge is not None and deliverable is not None:
        await _index_deliverable(knowledge, run, deliverable, reply_text, files, session)


def _extract_inline(output_ref: object) -> str:
    if not isinstance(output_ref, dict):
        return ""
    val = output_ref.get("inline")
    if isinstance(val, str):
        return val.strip()
    return ""


def _extract_founder_summary(output_ref: object) -> str:
    """Worker-authored 1-3 sentence summary for the founder chat.

    Distinct from ``inline`` which is the raw worker prose; this is the
    explicit "what to tell the founder" field. Workers should populate
    it; falls back to ``inline``.
    """
    if not isinstance(output_ref, dict):
        return ""
    val = output_ref.get("founder_summary")
    if isinstance(val, str):
        return val.strip()
    return ""


def _extract_files(output_ref: object) -> list[dict[str, Any]]:
    if not isinstance(output_ref, dict):
        return []
    val = output_ref.get("files")
    if not isinstance(val, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in val:
        if isinstance(entry, dict) and entry.get("path"):
            out.append(entry)
    return out


def _default_summary(files: list[dict[str, Any]]) -> str:
    """Fallback chat reply when the model wrote files but gave no prose."""
    if not files:
        return ""
    names = ", ".join(str(f.get("path")) for f in files[:5])
    suffix = "" if len(files) <= 5 else f" (+{len(files) - 5} more)"
    return f"Wrote {len(files)} file(s): {names}{suffix}"


async def _ensure_assistant_message(run: "ExecutionRun", reply_text: str, session: AsyncSession) -> None:
    """Write the per-iteration ``phase_done`` chat message.

    Replaces the old per-request dedupe with per-run dedupe so each
    iteration gets its own founder-facing summary. The replanner-issued
    ``phase_start`` and ``decision_request`` messages live in the same
    table tagged differently — they don't block this insert.
    """
    if run.request_id is None or not reply_text:
        return
    # One ``phase_done`` per run.
    stmt = select(ConversationMessage).where(
        ConversationMessage.request_id == run.request_id,
        ConversationMessage.role == "assistant",
    )
    rows = (await session.execute(stmt)).scalars().all()
    if any(_message_kind(r) == "phase_done" and _action_run_id(r) == str(run.id) for r in rows):
        return
    msg = ConversationMessage(
        project_id=run.project_id,
        role="assistant",
        content=reply_text,
        request_id=run.request_id,
        actions=[{"kind": "phase_done", "run_id": str(run.id)}],
    )
    session.add(msg)
    await session.flush()
    logger.info(
        "phase_done_message_recorded",
        run_id=str(run.id),
        request_id=str(run.request_id),
        message_id=str(msg.id),
    )


def _message_kind(msg: ConversationMessage) -> str | None:
    actions = msg.actions or []
    for a in actions:
        if isinstance(a, dict) and a.get("kind"):
            return str(a["kind"])
    return None


def _action_run_id(msg: ConversationMessage) -> str | None:
    actions = msg.actions or []
    for a in actions:
        if isinstance(a, dict) and a.get("run_id"):
            return str(a["run_id"])
    return None


async def insert_chat_event(
    session: AsyncSession,
    *,
    project_id,
    request_id,
    run_id,
    kind: str,
    content: str,
    extra: dict[str, Any] | None = None,
) -> ConversationMessage:
    """Append an assistant chat event with a typed ``actions`` tag.

    Used by the dispatcher for the chief-of-staff narration: phase_start,
    chain_done, decision_request. Always idempotent on (run_id, kind):
    re-running the same dispatcher phase shouldn't duplicate messages.
    """
    stmt = select(ConversationMessage).where(
        ConversationMessage.request_id == request_id,
        ConversationMessage.role == "assistant",
    )
    rows = (await session.execute(stmt)).scalars().all()
    for row in rows:
        if _message_kind(row) == kind and _action_run_id(row) == str(run_id):
            return row
    action: dict[str, Any] = {"kind": kind, "run_id": str(run_id)}
    if extra:
        action.update(extra)
    msg = ConversationMessage(
        project_id=project_id,
        role="assistant",
        content=content,
        request_id=request_id,
        actions=[action],
    )
    session.add(msg)
    await session.flush()
    logger.info("chat_event_recorded", kind=kind, run_id=str(run_id), message_id=str(msg.id))
    return msg


async def _ensure_deliverable(
    run: "ExecutionRun",
    reply_text: str,
    files: list[dict[str, Any]],
    session: AsyncSession,
) -> Deliverable | None:
    if run.request_id is None:
        return None
    # Dedupe per-run (planner-seeded phases share a request_id, so one
    # deliverable per phase is what the timeline needs).
    existing_stmt = select(DeliverableVersion.deliverable_id).where(
        DeliverableVersion.created_by_run_id == run.id,
    )
    if (await session.execute(existing_stmt)).scalar_one_or_none() is not None:
        return None

    request_stmt = select(Request).where(Request.id == run.request_id)
    request = (await session.execute(request_stmt)).scalar_one_or_none()
    title = _derive_title(reply_text, run, request)

    deliverable = Deliverable(
        tenant_id=run.tenant_id,
        project_id=run.project_id,
        request_id=run.request_id,
        type=_infer_type(files),
        title=title,
        status=DeliverableStatus.delivered,
    )
    session.add(deliverable)
    await session.flush()

    content_ref: dict[str, Any] = {"inline": reply_text}
    if files:
        content_ref["files"] = files

    payload_for_hash = (reply_text + "\n" + "\n".join(f"{f.get('path')}:{f.get('size')}" for f in files)).encode(
        "utf-8"
    )

    version = DeliverableVersion(
        deliverable_id=deliverable.id,
        version_int=1,
        storage_backend=StorageBackend.object,
        content_ref=content_ref,
        content_hash=hashlib.sha256(payload_for_hash).hexdigest(),
        size_bytes=sum(int(f.get("size") or 0) for f in files) or len(payload_for_hash),
        created_by_run_id=run.id,
    )
    session.add(version)
    await session.flush()
    deliverable.current_version_id = version.id
    await session.flush()
    logger.info(
        "deliverable_created",
        run_id=str(run.id),
        request_id=str(run.request_id),
        deliverable_id=str(deliverable.id),
        type=deliverable.type.value,
        files=len(files),
    )
    return deliverable


async def _index_deliverable(
    knowledge: "KnowledgeClient",
    run: "ExecutionRun",
    deliverable: Deliverable,
    reply_text: str,
    files: list[dict[str, Any]],
    session: AsyncSession,
) -> None:
    """Forward the raw run output to BSage's bsnexus-input webhook.

    Deliberately does NOT pre-compute a title or content — BSage's
    seed-refiner (AgentLoop._refine_seed) derives a concise title from
    the payload via LLM, matching the pattern every other BSage input
    plugin follows. Pre-computing here produced noisy names like
    'Wrote 6 file(s): package.json, tsconfig.json, ...'.
    """
    project = (await session.execute(select(Project).where(Project.id == run.project_id))).scalar_one_or_none()
    project_name = project.name if project is not None else "unknown-project"

    # Forward the founder's own JWT so BSage records the write under
    # their identity (same-account SSO). Falls back to the configured
    # api_key when no originator token is available.
    originator_token: str | None = None
    request_intent: str | None = None
    if run.request_id is not None:
        req = (await session.execute(select(Request).where(Request.id == run.request_id))).scalar_one_or_none()
        if req is not None:
            originator_token = req.originator_auth
            request_intent = req.intent_summary

    payload: dict[str, Any] = {
        "reply_text": reply_text,
        "files": files,
        "project": project_name,
        "run_id": str(run.id),
        "request_id": str(run.request_id) if run.request_id else None,
        "request_intent": request_intent,
        "deliverable_id": str(deliverable.id),
        "deliverable_type": deliverable.type.value,
        "source": f"bsnexus:{project_name}",
        "tags": [
            f"project:{_slug(project_name)}",
            f"type:{deliverable.type.value}",
            "bsnexus-deliverable",
        ],
    }
    ref = await knowledge.index(payload=payload, auth_token=originator_token)
    if ref is not None:
        logger.info(
            "deliverable_indexed_in_bsage",
            run_id=str(run.id),
            deliverable_id=str(deliverable.id),
        )


def _slug(text: str) -> str:
    import re as _re

    cleaned = _re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return cleaned or "project"


def _derive_title(
    reply_text: str,
    run: "ExecutionRun",
    request: "Request | None",
) -> str:
    """Pick a short descriptive title for the timeline card.

    Priority:
    1. First sentence of the assistant reply.
    2. The run's directive (planner phase prompt) if set.
    3. The request's intent_summary (founder's original wording).
    """
    summary = _first_sentence(reply_text)
    if summary:
        return summary[:200]
    if run.directive:
        return _first_line(run.directive)[:200]
    if request and request.intent_summary:
        return request.intent_summary[:200]
    return "Deliverable"


def _first_sentence(text: str) -> str | None:
    """Return the first meaningful sentence of a markdown doc."""
    import re as _re

    without_code = _re.sub(r"```[\s\S]*?```", "", text)
    for raw in without_code.splitlines():
        line = raw.strip()
        if not line:
            continue
        line = _re.sub(r"^[#*\->\s]+", "", line)
        line = line.strip("*_`\"'—– ")
        if not line:
            continue
        m = _re.search(r"[.!?。！？]\s", line)
        if m:
            return line[: m.end()].rstrip()
        return line
    return None


def _first_line(text: str) -> str:
    for raw in text.splitlines():
        line = raw.strip()
        if line:
            return line
    return text


def _infer_type(files: list[dict[str, Any]]) -> DeliverableType:
    """Rough type heuristic based on what was actually written."""
    if not files:
        return DeliverableType.doc
    paths = [str(f.get("path") or "").lower() for f in files]
    langs = [str(f.get("language") or "").lower() for f in files]
    if any(p.endswith((".html", ".htm")) for p in paths):
        return DeliverableType.design
    code_ext = (
        ".py",
        ".ts",
        ".tsx",
        ".jsx",
        ".js",
        ".mjs",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".swift",
        ".rb",
        ".php",
        ".cs",
        ".cpp",
        ".c",
    )
    if any(p.endswith(code_ext) for p in paths):
        return DeliverableType.code
    code_langs = {
        "python",
        "typescript",
        "javascript",
        "go",
        "rust",
        "java",
        "kotlin",
        "swift",
        "ruby",
        "php",
    }
    if any(lang in code_langs for lang in langs):
        return DeliverableType.code
    return DeliverableType.doc
