"""Post-run side effects — surface a completed run's output in the UI.

``ExecutionRun.output_ref`` alone isn't visible to the founder; two
things need to be materialised after a run transitions to ``done``:

1. An ``assistant``-role ``ConversationMessage`` on the originating
   request so the Direction chat gets an actual reply.
2. A ``Deliverable`` + ``DeliverableVersion`` row so the Progress tab
   surfaces the output as a proper artefact.

Both writes are idempotent: re-running on the same completed run is a
no-op. That matters because two code paths call this helper — the
sync ``_dispatch_new_run`` path in the conversation API, and the
async ``WorkerResultConsumer`` — and at-least-once Redis delivery
means the consumer path can fire twice for the same run.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.code_extractor import ExtractedFile, extract_files
from backend.src.core import workspace as workspace_store
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
    """Materialise a completed run's inline text output in the UI.

    Pre-conditions: ``run`` is attached to ``session`` and its
    ``status`` is ``done``. Callers usually load the run fresh before
    invoking this; we guard anyway.
    """
    if run.status != RunStatus.done:
        return
    inline = _extract_inline(run.output_ref)
    if not inline:
        logger.info("publish_run_output_no_inline", run_id=str(run.id))
        return

    extracted = extract_files(inline)
    _write_files_to_workspace(run.project_id, extracted)

    await _ensure_assistant_message(run, inline, session)
    await _ensure_deliverable(run, inline, extracted, session)


def _write_files_to_workspace(
    project_id: Any, files: list[ExtractedFile]
) -> None:
    for f in files:
        try:
            workspace_store.write_file(project_id, f.path, f.content)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "workspace_write_failed",
                project_id=str(project_id),
                path=f.path,
                error=str(exc),
            )


def _extract_inline(output_ref: object) -> str | None:
    if not isinstance(output_ref, dict):
        return None
    val = output_ref.get("inline")
    if isinstance(val, str) and val.strip():
        return val
    return None


async def _ensure_assistant_message(
    run: "ExecutionRun", inline: str, session: AsyncSession
) -> None:
    if run.request_id is None:
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
        content=inline,
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
    inline: str,
    files: list[ExtractedFile],
    session: AsyncSession,
) -> None:
    if run.request_id is None:
        return
    existing_stmt = select(Deliverable.id).where(
        Deliverable.request_id == run.request_id,
    )
    if (await session.execute(existing_stmt)).scalar_one_or_none() is not None:
        return

    request_stmt = select(Request).where(Request.id == run.request_id)
    request = (await session.execute(request_stmt)).scalar_one_or_none()
    title = (
        (request.intent_summary or "Deliverable")[:500] if request else "Deliverable"
    )

    deliverable = Deliverable(
        tenant_id=run.tenant_id,
        project_id=run.project_id,
        request_id=run.request_id,
        type=_infer_type(inline),
        title=title,
        status=DeliverableStatus.ready,
    )
    session.add(deliverable)
    await session.flush()

    encoded = inline.encode("utf-8")
    content_ref: dict[str, Any] = {"inline": inline}
    if files:
        content_ref["files"] = [
            {"path": f.path, "language": f.language, "size": len(f.content)}
            for f in files
        ]
    version = DeliverableVersion(
        deliverable_id=deliverable.id,
        version_int=1,
        storage_backend=StorageBackend.object,
        content_ref=content_ref,
        content_hash=hashlib.sha256(encoded).hexdigest(),
        size_bytes=len(encoded),
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
    )


def _infer_type(content: str) -> DeliverableType:
    """Rough type heuristic — real classification can come later.

    - Whole-HTML document → design
    - Fenced code blocks → code
    - Everything else → doc
    """
    lowered = content.lower()
    if "<!doctype html" in lowered or "<html" in lowered:
        return DeliverableType.design
    if any(
        fence in content
        for fence in (
            "```html",
            "```css",
            "```js",
            "```ts",
            "```tsx",
            "```jsx",
            "```python",
            "```go",
            "```rust",
            "```bash",
            "```sh",
        )
    ):
        return DeliverableType.code
    return DeliverableType.doc
