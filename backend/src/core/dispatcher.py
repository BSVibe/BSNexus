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
                # ``type: "http"`` is the modern claude-CLI mcpServers shape
                # for streamable-HTTP MCP (claude.com/docs/en/mcp). codex
                # accepts the same URL via TOML ``url`` field; opencode
                # auto-negotiates streamable-HTTP first when type=remote.
                # Single transport (streamable-HTTP at /mcp/http) covers
                # all three executors — SSE-only is deprecated by the MCP
                # spec.
                # Trailing slash is REQUIRED — Starlette mounts only
                # match the prefix when followed by ``/`` (or further
                # path), and the streamable-HTTP MCP client posts to
                # the URL verbatim. Without the slash the request hits
                # ``/mcp/http`` exactly, which is not a route, FastAPI
                # 404s, the client raises "Session terminated", and
                # the LLM tool loop falls back to no-tools mode.
                adapter.set_mcp_servers(
                    {
                        "bsnexus": {
                            "type": "http",
                            "url": f"{base}/mcp/http/?token={token}",
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
                    publish_run_output_chunk,
                )

                _captured_run_id = run.id
                _captured_project_id = run.project_id

                async def _on_chunk(text: str) -> None:
                    await publish_run_output_chunk(_captured_project_id, run_id=_captured_run_id, chunk=text)

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
            # Preserve any partial output the BSGatewayClient buffered
            # before the terminal error chunk. ``BSGatewayError.partial_output``
            # is empty for non-streaming or pre-stream HTTP failures.
            partial = getattr(exc, "partial_output", "") or ""
            result = {"_error": str(exc)}
            if partial:
                result["output_ref"] = {"inline": partial}
            # Recover any tool-call activity the adapter logged before
            # the failure — it's the most useful failure-mode signal
            # for diagnosing why a run blocked (PR7).
            partial_log = getattr(adapter, "_tool_activity_log", None)
            if partial_log:
                result["tool_activity_log"] = list(partial_log)

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
                # Route through RunStateMachine.transition so the run-history
                # row, milestone activity row, ``run_transition`` SSE event,
                # and ``nexus.run.blocked`` audit emit all fire (CLAUDE.md
                # NEVER rule). Direct status assignment used to skip all four
                # — pre-merge review caught the regression.
                from backend.src.core.state_machine import RunStateMachine  # noqa: PLC0415

                # Persist any partial output streamed before the failure so
                # the founder sees what claude actually produced rather than
                # a blank Inside panel after the stream cuts out.
                if isinstance(result.get("output_ref"), dict) and result["output_ref"].get("inline"):
                    run.output_type = "text"
                    run.output_ref = result["output_ref"]
                state_machine = RunStateMachine()
                await state_machine.transition(
                    run,
                    RunStatus.blocked,
                    reason=result["_error"],
                    actor="orchestrator",
                    db_session=session,
                    stream_manager=stream_manager,
                )
                # Persist any partial activity log even on the failure
                # path — failure-mode dashboard should still see what
                # the LLM tried before it blocked (PR7).
                partial_activity = result.get("tool_activity_log") or []
                if partial_activity:
                    from backend.src.core.llm.activity_persistence import (  # noqa: PLC0415
                        persist_tool_activity_log,
                    )

                    await persist_tool_activity_log(run, partial_activity, session)
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
                await publish_run_output(run, session, knowledge=knowledge, stream_manager=stream_manager)

            # Persist any tool-call / round activity log the adapter
            # accumulated during the LLM call (PR7 — failure-mode
            # instrumentation). BSGatewayAdapter doesn't populate this
            # key, so missing/empty list silently no-ops.
            activity_log = result.get("tool_activity_log") if isinstance(result, dict) else None
            if activity_log:
                from backend.src.core.llm.activity_persistence import (  # noqa: PLC0415
                    persist_tool_activity_log,
                )

                await persist_tool_activity_log(run, activity_log, session)

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

    # Two top-level executor kinds, distinguished by *infra dependency*
    # not by *capability* — both honor MCP / Decisions / artifact UX:
    #
    #   bsgateway — through BSVibe's BSGateway worker pool. Model
    #               string in ``cfg.model`` is whatever BSGateway
    #               routes (``claude_code``, ``openai/gpt-4o``, ...).
    #   llm_api   — direct LLM call from BSNexus (BSVibe optional).
    #               ``cfg.model`` is a litellm-style identifier
    #               (``anthropic/claude-3-7-sonnet`` etc.). MCP wired
    #               client-side through the tool loop in
    #               ``core.llm.direct_client``.
    _ = stream_manager  # noqa: F841 — retained for caller-compat
    _ = run_id  # noqa: F841

    api_key = _decrypt_api_key(row, cfg, tenant_id)
    if api_key is None:
        return None

    if exec_type == "bsgateway":
        return _build_bsgateway_adapter(row, cfg, tenant_id, project_id, api_key)
    if exec_type == "llm_api":
        return _build_llm_api_adapter(row, cfg, tenant_id, project_id, api_key)
    logger.warning(
        "executor_config_unknown_type",
        config_id=str(row.id),
        tenant_id=str(tenant_id),
        executor_type=exec_type,
    )
    return None


def _decrypt_api_key(
    row: ExecutorConfig,
    cfg: dict[str, Any],
    tenant_id: uuid.UUID,
) -> str | None:
    """Resolve the API key string for the executor.

    Returns ``"unused"`` when no key is configured (BSGateway has paths
    that don't require auth; ``DirectLLMAdapter`` will reject this and
    fail the dispatch loud). Returns ``None`` when an encrypted blob
    fails to decrypt — caller treats as a hard skip so we don't dispatch
    with a bad key.
    """
    if row.api_key_encrypted:
        from backend.src.config import settings as app_settings  # noqa: PLC0415
        from backend.src.core.encryption import EncryptionManager  # noqa: PLC0415

        try:
            return EncryptionManager(app_settings.encryption_key).decrypt_value(row.api_key_encrypted)
        except ValueError:
            logger.error(
                "executor_config_api_key_decrypt_failed",
                config_id=str(row.id),
                tenant_id=str(tenant_id),
            )
            return None
    if cfg.get("bsgateway_api_key") or cfg.get("api_key"):
        # Plaintext fallback for rows not re-saved through the API
        # after the 2026-05-04 encryption migration. WARN once so the
        # operator notices and rotates.
        logger.warning(
            "executor_config_plaintext_api_key_in_use",
            config_id=str(row.id),
            tenant_id=str(tenant_id),
            hint="re-save the config through PATCH /api/v1/executor-configs/{id} to encrypt at rest",
        )
        return cfg.get("bsgateway_api_key") or cfg.get("api_key") or "unused"
    return "unused"


def _build_bsgateway_adapter(
    row: ExecutorConfig,
    cfg: dict[str, Any],
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    api_key: str,
) -> Any | None:
    gateway_url = cfg.get("bsgateway_url") or cfg.get("base_url")
    if not gateway_url:
        logger.warning(
            "executor_config_bsgateway_missing_url",
            config_id=str(row.id),
            tenant_id=str(tenant_id),
        )
        return None
    model = cfg.get("model") or "claude_code"

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


def _build_llm_api_adapter(
    row: ExecutorConfig,
    cfg: dict[str, Any],
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    api_key: str,
) -> Any | None:
    """Build the direct-LLM adapter for ``executor_type=llm_api`` —
    BSVibe-optional path that calls the LLM provider directly via
    litellm + a client-side MCP tool loop.
    """
    try:
        from backend.src.core.llm.direct_client import DirectLLMAdapter  # noqa: PLC0415
    except ImportError:
        logger.error(
            "executor_config_llm_api_not_implemented",
            config_id=str(row.id),
            tenant_id=str(tenant_id),
            hint="DirectLLMAdapter ships in core.llm.direct_client — see ~/Docs/BSNexus_Direction_2026-05-03.md",
        )
        return None

    model = cfg.get("model")
    if not model:
        logger.warning(
            "executor_config_llm_api_missing_model",
            config_id=str(row.id),
            tenant_id=str(tenant_id),
        )
        return None

    # Optional ``base_url`` for self-hosted / Tailscale-routed LLMs
    # (e.g. ollama on the Mac Mini reached via 100.x.x.x:11434).
    # Empty string is treated as "unset" so accidentally-saved blanks
    # don't break litellm provider defaults.
    api_base = (cfg.get("base_url") or "").strip() or None

    if not api_key or api_key == "unused":
        if api_base is None:
            # Cloud LLM (no base_url): missing key would 401 at the
            # boundary. Refuse upfront with a WARN.
            logger.warning(
                "executor_config_llm_api_missing_api_key",
                config_id=str(row.id),
                tenant_id=str(tenant_id),
            )
            return None
        # Self-hosted endpoint (base_url set, e.g. ollama / vLLM).
        # litellm needs *some* api_key kwarg even when the upstream
        # ignores it — empty string trips its missing-key path on
        # certain provider routers. Use a sentinel placeholder.
        api_key = "self-hosted-no-auth"

    from backend.src.core.project_workspace import project_workspace_path  # noqa: PLC0415

    workspace_dir = str(project_workspace_path(project_id))
    return DirectLLMAdapter(
        model=model,
        api_key=api_key,
        project_id=project_id,
        workspace_dir=workspace_dir,
        api_base=api_base,
    )
