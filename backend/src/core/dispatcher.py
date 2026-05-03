"""Fire-and-forget run dispatch helper.

Spawns a background task that runs ``RunOrchestrator.dispatch_run`` on
its own DB session so the caller (an HTTP handler, or the orchestrator
itself after completing the previous phase) returns immediately.

Extracted from ``api/conversation.py`` so the orchestrator can enqueue
its own successors without creating an import cycle.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.composer import resolve_knowledge_client
from backend.src.core.integrations import get_tenant_integration_snapshot
from backend.src.core.run_artifacts import publish_run_output
from backend.src.core.run_orchestrator import get_run_orchestrator
from backend.src.models import ExecutorConfig, RunStatus
from backend.src.storage.database import async_session

logger = structlog.get_logger(__name__)


def fire_run(
    run_id: uuid.UUID,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    stream_manager: Any | None,
) -> asyncio.Task:
    """Schedule ``_dispatch_background`` as a new task and return it.

    Callers typically ignore the returned task — the background work
    finalizes the run in its own session + commits independently.
    """
    return asyncio.create_task(_dispatch_background(run_id, tenant_id, project_id, stream_manager))


async def _dispatch_background(
    run_id: uuid.UUID,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    stream_manager: Any | None,
) -> None:
    """Run dispatch across three session scopes so the DB pool isn't
    pinned for the full LLM round-trip.

    Phase 1 (short session): build adapter + run ``dispatch_run`` with
    ``executor=None`` so the orchestrator only does compose →
    snapshot → state transition + COMMIT, then returns. The session is
    released back to the pool before the network call.

    Phase 2 (no session): call ``adapter.execute(...)`` for the actual
    LLM turn. This is the minutes-long step; holding a DB connection
    here would starve concurrent HTTP requests (especially DELETE
    project, which blocks behind the row lock).

    Phase 3 (short session): re-attach the run in a fresh session and
    finalize via ``on_run_completed`` → ``publish_run_output``.
    """
    # Direction reset 2026-05-03 — Phase 0 (LLM-driven replanner) is
    # retired. Request → Run is 1:1 now; BSGateway's CLI agent does its
    # own reasoning inside the worker. ``replan_next_step`` and the
    # decision-creation-during-dispatch branch are gone with this commit.
    try:
        # Phase 1: prepare + transition to running, commit, release.
        prepared: dict[str, Any] | None = None
        async with async_session() as session:
            adapter = await build_adapter(
                session,
                tenant_id,
                run_id=run_id,
                project_id=project_id,
                stream_manager=stream_manager,
            )
            if adapter is None:
                # No executor configured — orchestrator's normal path
                # (with executor=None) still transitions to running and
                # awaits an external callback.
                await get_run_orchestrator().dispatch_run(
                    run_id,
                    db=session,
                    executor=None,
                    stream_manager=stream_manager,
                )
                return

            run = await get_run_orchestrator().dispatch_run(
                run_id,
                db=session,
                executor=None,  # prep-only; we run the LLM ourselves below
                stream_manager=stream_manager,
            )
            if run is None or run.status != RunStatus.running:
                # Blocked by audit, etc. — nothing to finalize.
                return

            # Gather everything the LLM needs before closing the session.
            from backend.src.core.run_orchestrator import (  # noqa: PLC0415
                _load_chat_history,
                _load_request,
            )
            from backend.src.models import CompositionSnapshot  # noqa: PLC0415

            request = await _load_request(session, run.request_id)
            history = await _load_chat_history(
                session,
                project_id=run.project_id,
                origin_message_id=request.origin_message_id,
            )
            snapshot_row = (
                await session.execute(
                    select(CompositionSnapshot).where(CompositionSnapshot.id == run.composition_snapshot_id)
                )
            ).scalar_one()

            # Plumb run audit metadata so BSGateway's pre/post hooks can
            # forward run.pre / run.post events to BSupervisor on
            # BSNexus's behalf. Both BSGatewayAdapter and the legacy
            # LiteLLMOrchestratorAdapter expose ``set_run_audit_metadata``.
            from backend.src.core.orchestrator_adapter import (  # noqa: PLC0415
                build_run_audit_metadata,
            )

            if hasattr(adapter, "set_run_audit_metadata"):
                adapter.set_run_audit_metadata(build_run_audit_metadata(run=run, snapshot=snapshot_row))

            # Direction reset 2026-05-03 — mint a run-scoped MCP token
            # and inject the BSNexus MCP server URL so the BSGateway
            # worker's claude CLI can call back via decision.create /
            # decision.wait / artifact.list / knowledge.search.
            if hasattr(adapter, "set_mcp_servers"):
                from backend.src.config import settings as _settings  # noqa: PLC0415
                from backend.src.mcp import issue_run_scoped_token  # noqa: PLC0415

                token = issue_run_scoped_token(
                    {
                        "run_id": str(run.id),
                        "tenant_id": str(run.tenant_id),
                        "project_id": str(run.project_id),
                    },
                    signing_key=_settings.mcp_signing_key,
                    # exp = run timeout + 5 min grace (BSGateway per-call
                    # timeout default is 3600s).
                    ttl_seconds=3600 + 300,
                )
                base = _settings.mcp_internal_url.rstrip("/")
                adapter.set_mcp_servers(
                    {
                        "bsnexus": {
                            "url": f"{base}/mcp/sse?token={token}",
                            "headers": {},
                        }
                    }
                )

            # Direction reset 2026-05-03 — Inside panel live streaming.
            # Each delta.content chunk from BSGateway becomes a run_output
            # event on the project SSE bus so the founder watches claude
            # type in real time.
            if hasattr(adapter, "set_on_chunk"):
                from backend.src.core.project_events import (  # noqa: PLC0415
                    publish_run_output as _publish_run_output_event,
                )

                _captured_run_id = run.id
                _captured_project_id = run.project_id

                async def _on_chunk(text: str) -> None:
                    await _publish_run_output_event(
                        _captured_project_id, run_id=_captured_run_id, chunk=text
                    )

                adapter.set_on_chunk(_on_chunk)

            prepared = {
                "adapter": adapter,
                "system_prompt": (snapshot_row.system_prompt_ref or {}).get("inline", ""),
                "user_prompt": run.directive or request.intent_summary,
                "tools_allowed": list(snapshot_row.tools_allowed or []),
                "history": history,
            }

        # Phase 2: run the LLM with no DB session held.
        assert prepared is not None
        adapter = prepared["adapter"]
        try:
            result = await adapter.execute(
                prepared["system_prompt"],
                prepared["user_prompt"],
                tools_allowed=prepared["tools_allowed"],
                history=prepared["history"],
            )
        except asyncio.CancelledError:
            # Cooperative cancellation: don't swallow — let the
            # supervising task finalize the run as cancelled.
            raise
        except Exception as exc:  # noqa: BLE001 — sink-all at the LLM boundary
            logger.warning("llm_execute_failed", run_id=str(run_id), exc_info=True)
            result = {"_error": str(exc)}

        # Async / worker executors return a "dispatched" sentinel —
        # they'll finalize via the worker-result consumer, not here.
        if isinstance(result, dict) and result.get("status") == "dispatched":
            return

        # Phase 3: finalize in a fresh session.
        async with async_session() as session:
            integrations = await get_tenant_integration_snapshot(session, tenant_id)
            # Re-load the run — ORM object from phase 1 is detached.
            from backend.src.models import ExecutionRun  # noqa: PLC0415

            run = (await session.execute(select(ExecutionRun).where(ExecutionRun.id == run_id))).scalar_one_or_none()
            if run is None:
                # Project was deleted while the LLM was running — nothing
                # to finalize. Not an error.
                logger.info("run_disappeared_during_llm", run_id=str(run_id))
                return

            originator_token: str | None = None
            if run.request_id is not None:
                from backend.src.models import Request as _Request  # noqa: PLC0415

                req_row = (
                    await session.execute(select(_Request).where(_Request.id == run.request_id))
                ).scalar_one_or_none()
                if req_row is not None:
                    originator_token = req_row.originator_auth

            if isinstance(result, dict) and "_error" in result:
                from backend.src.models import RunStatus as _RunStatus  # noqa: PLC0415

                run.status = _RunStatus.blocked
                run.error_message = result["_error"]
                await session.commit()
                return

            await get_run_orchestrator().on_run_completed(
                run,
                result=result,
                db=session,
                stream_manager=stream_manager,
            )

            if run.status == RunStatus.done:
                knowledge = resolve_knowledge_client(integrations.bsage, auth_token=originator_token)
                await publish_run_output(run, session, knowledge=knowledge)
            await session.commit()
    except asyncio.CancelledError:
        logger.info("background_dispatch_cancelled", run_id=str(run_id))
        raise
    except Exception:  # noqa: BLE001 — top-level guard for the fire-and-forget task
        logger.error("background_dispatch_failed", run_id=str(run_id), exc_info=True)


async def build_adapter(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    run_id: uuid.UUID,
    project_id: uuid.UUID,
    stream_manager: Any | None = None,
) -> Any | None:
    """Pick an executor adapter for the tenant's default ExecutorConfig.

    Mirrors ``api/conversation._build_adapter``. Kept here too so the
    orchestrator's successor-dispatch path doesn't re-import the
    HTTP-facing module.
    """
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

    exec_type = (row.executor_type or "").lower()
    cfg = row.config or {}

    # Direction reset 2026-05-03 — all executor types route through
    # BSGateway via BSGatewayAdapter. The differentiator is the ``model``
    # string sent to BSGateway:
    #   - claude_code / codex / opencode ⇒ literal as the model name
    #   - bsgateway / generic_llm        ⇒ cfg.model (LiteLLM-routed)
    # gateway_url + bsgateway_api_key live on the ExecutorConfig row.
    # ``stream_manager`` and ``run_id`` are kept in the signature for the
    # cutover window — old callers / tests still pass them.
    _ = stream_manager  # noqa: F841 — retained for caller-compat
    _ = run_id  # noqa: F841

    gateway_url = cfg.get("bsgateway_url") or cfg.get("base_url")
    if not gateway_url:
        return None
    api_key = cfg.get("bsgateway_api_key") or cfg.get("api_key") or "unused"

    if exec_type in {"claude_code", "codex", "opencode"}:
        model = exec_type
    elif exec_type in {"bsgateway", "generic_llm", "worker"}:
        model = cfg.get("model") or "claude_code"
    else:
        return None

    from backend.src.core.bsgateway import BSGatewayAdapter, BSGatewayClient  # noqa: PLC0415
    from backend.src.core.project_workspace import project_workspace_path  # noqa: PLC0415

    client = BSGatewayClient(base_url=gateway_url, api_key=api_key)
    workspace_dir = str(project_workspace_path(project_id))
    return BSGatewayAdapter(
        client=client,
        model=model,
        project_id=project_id,
        run_audit_metadata=None,  # dispatcher patches via set_run_audit_metadata
        workspace_dir=workspace_dir,
    )
