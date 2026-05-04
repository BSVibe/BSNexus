"""Tool implementations the BSNexus MCP server exposes to BSGateway worker CLIs.

These are plain async functions so the unit tests don't have to fight
the MCP protocol layer — the server module is a thin wrapper that
dispatches MCP calls through to these.

All tools enforce tenant scoping defence-in-depth: even though the
SSE handler verifies the run-scoped token first, the tools also check
the row's ``tenant_id`` matches the verified claim's. Cross-tenant
attempts return empty (for list/read endpoints) or raise
``MCPToolError`` (for write/wait endpoints).

v1 ships six tools: ``decision.create``, ``decision.wait``,
``artifact.list``, ``artifact.read``, ``report_deliverable``,
``knowledge.search``. ``report_deliverable`` writes inline content
into a DeliverableVersion (``StorageBackend.object``); fetching
non-inline content (S3 GET, git show) for ``artifact.read`` rides a
follow-up — see ``BSNexus_BSGateway_TODOs_2026-05-04.md`` items #1/#2.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.composer.knowledge_client import KnowledgeClient
from backend.src.mcp.decision_queue import DecisionQueue, DecisionWaitTimeout
from backend.src.models import (
    Decision,
    Deliverable,
    DeliverableStatus,
    DeliverableType,
    DeliverableVersion,
    ExecutionRun,
    StorageBackend,
)

logger = structlog.get_logger(__name__)


class MCPToolError(Exception):
    """Raised when a tool call fails authorization or pre-conditions.

    The MCP server surfaces this to the CLI as a tool-call error result,
    which claude turns into a recoverable error in its own loop (it
    typically retries with a different prompt or surfaces to the
    founder via a different channel).
    """


# ─── decision.create ──────────────────────────────────────────────────


async def create_decision(
    *,
    question: str,
    options: list[str],
    context: str | None,
    run_id: uuid.UUID,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    db: AsyncSession,
) -> uuid.UUID:
    """Persist a Decision row tied to ``run_id`` and surface it to the
    founder via the existing Decisions inbox.

    Cross-tenant: the ``run_id`` must belong to ``tenant_id``. We re-
    check here even though the SSE handler verified the token because
    a defective token claim should not silently leak rows.
    """
    run = await db.get(ExecutionRun, run_id)
    if run is None or run.tenant_id != tenant_id:
        raise MCPToolError("run_id not in tenant scope")

    # Decision rows don't carry a free-form ``context`` column today;
    # claude prepends informational context into the question itself
    # when it wants the founder to see it. We do not silently coerce
    # it into ``options``.
    full_question = question if not context else f"{question}\n\nContext: {context}"
    decision = Decision(
        tenant_id=tenant_id,
        project_id=project_id,
        request_id=run.request_id,
        origin_run_id=run_id,
        question=full_question,
        options=list(options),
        blocking=True,
    )
    db.add(decision)
    await db.flush()
    logger.info(
        "mcp_decision_created",
        decision_id=str(decision.id),
        run_id=str(run_id),
        tenant_id=str(tenant_id),
    )
    return decision.id


# ─── decision.wait ────────────────────────────────────────────────────


async def wait_for_decision(
    *,
    decision_id: uuid.UUID,
    tenant_id: uuid.UUID,
    queue: DecisionQueue,
    timeout_seconds: float,
    db: AsyncSession,
) -> dict[str, Any]:
    """Long-poll for the founder to resolve ``decision_id``.

    Resolution path:

    1. **Already resolved in DB** — happens after a 504 timeout-retry
       or a process restart. We return the persisted choice/notes and
       let the CLI continue without round-tripping the queue.
    2. **In-flight** — register on the queue and ``await`` the
       Event. The decisions API's resolve handler calls
       ``queue.notify`` when the founder picks an option.
    3. **Timeout** — bubble up ``MCPToolError`` so the CLI can decide
       (usually it surfaces an "awaiting decision" deliverable).
    """
    decision = await db.get(Decision, decision_id)
    if decision is None:
        raise MCPToolError("decision not found")
    if decision.tenant_id != tenant_id:
        raise MCPToolError("decision not in tenant scope")

    if decision.resolution is not None:
        # Already resolved — return the persisted resolution. The
        # ``resolution`` field is currently a single text string; we
        # surface it under the ``choice`` key so the MCP shape matches
        # the in-flight branch (``{choice, notes}``).
        return {"choice": decision.resolution, "notes": ""}

    queue.register(decision_id)
    try:
        result = await queue.wait_for(decision_id, timeout_seconds=timeout_seconds)
    except DecisionWaitTimeout as exc:
        raise MCPToolError(str(exc)) from exc

    return {
        "choice": result.get("choice"),
        "notes": result.get("notes", ""),
    }


# ─── artifact.list ────────────────────────────────────────────────────


async def list_run_artifacts(
    *,
    request_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: AsyncSession,
) -> list[dict[str, Any]]:
    """Return the deliverables already produced for the request — lets
    the CLI inspect prior outputs before deciding what to write.

    Cross-tenant returns ``[]`` (defensive — not 404), matching the
    rest of BSNexus's tenant-scope behaviour.
    """
    stmt = (
        select(Deliverable)
        .where(
            Deliverable.tenant_id == tenant_id,
            Deliverable.request_id == request_id,
        )
        .order_by(Deliverable.created_at.asc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "id": str(row.id),
            "title": row.title,
            "type": row.type.value,
            "status": row.status.value,
        }
        for row in rows
    ]


# ─── knowledge.search ─────────────────────────────────────────────────


async def search_knowledge(
    *,
    query: str,
    knowledge_client: KnowledgeClient | None,
    top_k: int = 10,
) -> list[dict[str, str]]:
    """Proxy through to BSage's knowledge graph. ``None`` client (BSage
    disabled / unreachable) returns ``[]`` so claude keeps working in
    degraded mode."""
    if knowledge_client is None:
        return []
    fragments = await knowledge_client.search(query, top_k=top_k)
    return [{"title": f.title, "excerpt": f.excerpt} for f in fragments]


# ─── report_deliverable ───────────────────────────────────────────────


_TYPE_KEYWORDS: tuple[tuple[DeliverableType, tuple[str, ...]], ...] = (
    (DeliverableType.code, ("```", ".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go")),
    (DeliverableType.design, ("figma", ".fig", ".bsd", "wireframe")),
    (DeliverableType.data, (".csv", ".json", ".parquet")),
)


def _infer_deliverable_type(title: str, body: str) -> DeliverableType:
    """Guess the deliverable type from title+body. Defaults to ``doc``."""
    haystack = f"{title}\n{body}".lower()
    for typ, keywords in _TYPE_KEYWORDS:
        if any(k in haystack for k in keywords):
            return typ
    return DeliverableType.doc


async def report_deliverable(
    *,
    title: str,
    body: str,
    links: list[str] | None,
    run_id: uuid.UUID,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    db: AsyncSession,
) -> uuid.UUID:
    """Persist a Deliverable + DeliverableVersion claude wrote during a run.

    Mirrors the orchestrator-driven ``run_artifacts._ensure_deliverable``
    path but for the MCP-driven case (claude announces "here's what I
    produced" mid-run). The caller (the MCP server) issues the commit.

    Cross-tenant: the ``run_id`` must belong to ``tenant_id``. Same
    defence-in-depth as ``create_decision``.
    """
    run = await db.get(ExecutionRun, run_id)
    if run is None or run.tenant_id != tenant_id:
        raise MCPToolError("run_id not in tenant scope")

    deliverable = Deliverable(
        tenant_id=tenant_id,
        project_id=project_id,
        request_id=run.request_id,
        type=_infer_deliverable_type(title, body),
        title=title[:500],
        status=DeliverableStatus.delivered,
    )
    db.add(deliverable)
    await db.flush()

    content_ref: dict[str, Any] = {"inline": body}
    if links:
        content_ref["links"] = list(links)
    payload_for_hash = (title + "\n" + body + "\n" + "\n".join(links or [])).encode("utf-8")
    version = DeliverableVersion(
        deliverable_id=deliverable.id,
        version_int=1,
        storage_backend=StorageBackend.object,
        content_ref=content_ref,
        content_hash=hashlib.sha256(payload_for_hash).hexdigest(),
        size_bytes=len(payload_for_hash),
        created_by_run_id=run_id,
    )
    db.add(version)
    await db.flush()
    deliverable.current_version_id = version.id
    await db.flush()
    logger.info(
        "mcp_deliverable_reported",
        deliverable_id=str(deliverable.id),
        run_id=str(run_id),
        tenant_id=str(tenant_id),
        type=deliverable.type.value,
    )
    return deliverable.id


# ─── artifact.read ────────────────────────────────────────────────────


async def read_artifact(
    *,
    deliverable_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: AsyncSession,
) -> str:
    """Return the inline body of a Deliverable's current version.

    For object/git storage backends with non-inline content_ref shapes,
    we return the raw inline string when present, else ``""``. Storage-
    backend-specific fetching (S3 GET, git show) is a follow-up — the
    primary path during M0 is inline content claude wrote via
    ``report_deliverable``.

    Cross-tenant raises ``MCPToolError``; missing deliverable raises
    ``MCPToolError`` with ``"not found"`` so the SSE handler can map
    cleanly to a tool-call error result.
    """
    deliverable = await db.get(Deliverable, deliverable_id)
    if deliverable is None:
        raise MCPToolError("deliverable not found")
    if deliverable.tenant_id != tenant_id:
        raise MCPToolError("deliverable not in tenant scope")
    if deliverable.current_version_id is None:
        return ""
    version = await db.get(DeliverableVersion, deliverable.current_version_id)
    if version is None or not isinstance(version.content_ref, dict):
        return ""
    inline = version.content_ref.get("inline")
    return inline if isinstance(inline, str) else ""
