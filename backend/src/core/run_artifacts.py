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
    Request,
    RunStatus,
    StorageBackend,
)

if TYPE_CHECKING:
    from backend.src.models import ExecutionRun

logger = structlog.get_logger(__name__)


async def publish_run_output(
    run: "ExecutionRun", session: AsyncSession
) -> None:
    """Materialise a completed run's chat reply + deliverable in the UI.

    Pre-conditions: ``run`` is attached to ``session`` and its
    ``status`` is ``done``. Callers usually load the run fresh before
    invoking this; we guard anyway.
    """
    if run.status != RunStatus.done:
        return
    inline = _extract_inline(run.output_ref)
    files = _extract_files(run.output_ref)

    if not inline and not files:
        logger.info("publish_run_output_empty", run_id=str(run.id))
        return

    reply_text = inline or _default_summary(files)
    await _ensure_assistant_message(run, reply_text, session)
    await _ensure_deliverable(run, reply_text, files, session)


def _extract_inline(output_ref: object) -> str:
    if not isinstance(output_ref, dict):
        return ""
    val = output_ref.get("inline")
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


async def _ensure_assistant_message(
    run: "ExecutionRun", reply_text: str, session: AsyncSession
) -> None:
    if run.request_id is None or not reply_text:
        return
    stmt = select(ConversationMessage.id).where(
        ConversationMessage.request_id == run.request_id,
        ConversationMessage.role == "assistant",
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        return
    msg = ConversationMessage(
        project_id=run.project_id,
        role="assistant",
        content=reply_text,
        request_id=run.request_id,
    )
    session.add(msg)
    await session.flush()
    logger.info(
        "assistant_reply_recorded",
        run_id=str(run.id),
        request_id=str(run.request_id),
        message_id=str(msg.id),
    )


async def _ensure_deliverable(
    run: "ExecutionRun",
    reply_text: str,
    files: list[dict[str, Any]],
    session: AsyncSession,
) -> None:
    if run.request_id is None:
        return
    # Dedupe per-run (planner-seeded phases share a request_id, so one
    # deliverable per phase is what the timeline needs).
    existing_stmt = select(DeliverableVersion.deliverable_id).where(
        DeliverableVersion.created_by_run_id == run.id,
    )
    if (await session.execute(existing_stmt)).scalar_one_or_none() is not None:
        return

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

    payload_for_hash = (reply_text + "\n" + "\n".join(
        f"{f.get('path')}:{f.get('size')}" for f in files
    )).encode("utf-8")

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
        ".py", ".ts", ".tsx", ".jsx", ".js", ".mjs", ".go", ".rs",
        ".java", ".kt", ".swift", ".rb", ".php", ".cs", ".cpp", ".c",
    )
    if any(p.endswith(code_ext) for p in paths):
        return DeliverableType.code
    code_langs = {
        "python", "typescript", "javascript", "go", "rust",
        "java", "kotlin", "swift", "ruby", "php",
    }
    if any(lang in code_langs for lang in langs):
        return DeliverableType.code
    return DeliverableType.doc
