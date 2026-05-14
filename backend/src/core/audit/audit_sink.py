"""AuditSink — BSupervisor integration for **non-LLM** workflow / budget audit.

LLM run.pre / run.post events have moved to BSGateway (Lockin
§Architectural shifts #1). Phase 0 P0.7 retires every BSNexus call site
that called ``audit.preflight()`` or ``emit_post_async()`` for an
ExecutionRun. BSGateway's LiteLLM async_pre_call_hook /
async_post_call_hook now sends those events to BSupervisor on BSNexus's
behalf — see ``orchestrator_adapter.build_run_audit_metadata`` for the
metadata that BSGateway receives.

Why this module survives
~~~~~~~~~~~~~~~~~~~~~~~~
The Lockin shift carves out an **explicit exception**: non-LLM workflow
and budget audits stay as BSNexus → BSupervisor direct calls. Future
features (e.g. founder-driven manual budget approval audit, scheduled
workflow correctness checks) will reuse ``BSupervisorAuditSink`` and
``NoopAuditSink``. The auth provider on those calls is a service JWT
(P0.7 §service-jwt swap) — the legacy static-api-key path is no longer
consulted when a ``service_jwt_minter`` is plumbed through the factory.

``NoopAuditSink`` is the fallback when BSupervisor is disabled for the
tenant — logs via structlog only, always allows.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

import httpx
import structlog

from backend.src.core.clients import BaseServiceClient
from backend.src.core.integrations.config import AuditProviderConfig

if TYPE_CHECKING:
    from backend.src.core.service_auth import ServiceJWTMinter
    from backend.src.models.composition_snapshot import CompositionSnapshot
    from backend.src.models.execution_run import ExecutionRun

logger = structlog.get_logger(__name__)

_USER_AGENT = "BSNexus/0.2 (+https://nexus.bsvibe.dev)"


@dataclass(frozen=True)
class AuditResult:
    """Outcome of a pre-run audit.

    ``degraded=True`` means BSupervisor didn't produce a real verdict —
    the value of ``blocked`` reflects fail-open/closed policy, not actual
    rule evaluation. Surfaced in the Inside panel.
    """

    blocked: bool
    reason: str | None = None
    degraded: bool = False


class AuditSink(Protocol):
    async def preflight(self, run: "ExecutionRun", snapshot: "CompositionSnapshot") -> AuditResult: ...

    async def emit_post(self, run: "ExecutionRun", result: Any) -> None: ...


class NoopAuditSink:
    """Fallback when BSupervisor isn't configured. Always allow."""

    async def preflight(self, run: "ExecutionRun", snapshot: "CompositionSnapshot") -> AuditResult:
        logger.debug("audit_noop_preflight", run_id=str(run.id))
        return AuditResult(blocked=False)

    async def emit_post(self, run: "ExecutionRun", result: Any) -> None:
        logger.debug("audit_noop_post", run_id=str(run.id))


class BSupervisorAuditSink:
    """Calls existing BSupervisor POST /api/events.

    Composes ``BaseServiceClient`` for the shared Bearer + UA + timeout
    + fail-soft scaffolding (S2-1-X). The auth provider closure can be
    swapped at runtime in Phase 0 P0.7 to mint service JWTs without
    touching this adapter.

    Fail-mode:
    - ``open`` (default): on timeout/error return ``blocked=False,
      degraded=True`` so runs proceed.
    - ``closed``: on timeout/error return ``blocked=True, degraded=True``
      so nothing runs without audit.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        *,
        auth_token: str | None = None,
        timeout_ms: int = 200,
        fail_mode: str = "open",
    ):
        self._fail_mode = fail_mode
        # Auth precedence mirrors BSageKnowledgeClient: forwarded SSO
        # JWT (from the founder's HTTP request) takes precedence over a
        # static api_key. Without either, BSupervisor's @protected
        # routes 401 — which is what was happening in production.
        self._instance_token = auth_token or api_key or ""
        self._base = BaseServiceClient(
            base_url=base_url,
            auth_provider=lambda: self._instance_token,
            user_agent=_USER_AGENT,
            timeout_s=timeout_ms / 1000.0,
        )
        # Backwards-compat snapshot for tests inspecting ``_headers``.
        self._headers: dict[str, str] = {"User-Agent": _USER_AGENT}
        if self._instance_token:
            self._headers["Authorization"] = f"Bearer {self._instance_token}"

    def _fail_result(self, reason: str) -> AuditResult:
        blocked = self._fail_mode == "closed"
        return AuditResult(blocked=blocked, reason=reason, degraded=True)

    async def preflight(self, run: "ExecutionRun", snapshot: "CompositionSnapshot") -> AuditResult:
        payload = {
            "event_type": "run.pre",
            "mode": "preflight",
            "tenant_id": str(run.tenant_id),
            "run_id": str(run.id),
            "project_id": str(run.project_id),
            "composition_id": str(snapshot.id),
            "tools_allowed": snapshot.tools_allowed,
            "persona_label": snapshot.persona_label,
        }
        try:
            resp = await self._base.request("POST", "/api/events", json=payload)
            resp.raise_for_status()
            body = resp.json()
        except asyncio.CancelledError:
            raise
        except httpx.TimeoutException:
            logger.warning("bsupervisor_preflight_timeout", run_id=str(run.id))
            return self._fail_result("preflight timeout")
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "bsupervisor_preflight_failed",
                run_id=str(run.id),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return self._fail_result(f"preflight error: {exc}")

        allowed = bool(body.get("allowed", True))
        return AuditResult(
            blocked=not allowed,
            reason=body.get("reason"),
            degraded=False,
        )

    async def emit_post(self, run: "ExecutionRun", result: Any) -> None:
        payload = {
            "event_type": "run.post",
            "mode": "log_only",
            "tenant_id": str(run.tenant_id),
            "run_id": str(run.id),
            "status": getattr(run, "status", None),
            "actual_cost_cents": getattr(run, "actual_cost_cents", 0),
            "result_summary": _summarize_result(result),
        }
        resp = await self._base.safe_request(
            "POST",
            "/api/events",
            event="bsupervisor_post_failed",
            json=payload,
        )
        if resp is None:
            return
        try:
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning(
                "bsupervisor_post_failed",
                run_id=str(run.id),
                error=str(exc),
                error_type=type(exc).__name__,
            )


def _summarize_result(result: Any) -> dict:
    if isinstance(result, dict):
        return {k: v for k, v in result.items() if k in {"status", "output_type", "error"}}
    return {"type": type(result).__name__}


def resolve_audit_sink(
    cfg: AuditProviderConfig | None,
    *,
    auth_token: str | None = None,
    service_jwt_minter: "ServiceJWTMinter | None" = None,
    tenant_id: str | None = None,
) -> AuditSink:
    """Factory: return BSupervisor sink when configured, Noop otherwise.

    Auth precedence (P0.7 onwards):
      1. ``service_jwt_minter`` + ``tenant_id`` — minted service JWT
         (Decision #16). The legacy static api_key on the integration
         row is NOT consulted when the minter is in play.
      2. ``auth_token`` — forwarded user SSO JWT (founder same-account
         attribution). Phase A fallback.
      3. ``cfg.api_key`` — legacy static key (only when neither minter
         nor SSO token present). Phase A drop in scope, no DB
         migration this PR per Lockin §3 D-O8.

    With none of the three set against an enabled config, the sink
    would always 401 against ``*.bsvibe.dev`` BSupervisor; we degrade
    to Noop so the chat doesn't fill with audit warnings.
    """
    if cfg is None or not cfg.enabled or not cfg.base_url:
        return NoopAuditSink()

    # P0.7 — service JWT is the new auth path. Swap the BaseServiceClient
    # auth_provider closure rather than passing a static token through
    # the adapter constructor (Decision #15 — adapter code unchanged).
    if service_jwt_minter is not None and tenant_id is not None:
        sink = BSupervisorAuditSink(
            cfg.base_url,
            api_key=None,  # legacy api_key is NOT consulted with minter present
            auth_token=None,
            timeout_ms=cfg.timeout_ms,
            fail_mode=cfg.fail_mode,
        )
        sink._base.set_auth_provider(
            service_jwt_minter.make_auth_provider(
                audience="bsupervisor",
                tenant_id=tenant_id,
                scope=["bsupervisor:audit.write"],
            )
        )
        return sink

    if not auth_token and not cfg.api_key:
        # No way to authenticate → BSupervisor will 401. Don't bother.
        return NoopAuditSink()
    return BSupervisorAuditSink(
        cfg.base_url,
        cfg.api_key,
        auth_token=auth_token,
        timeout_ms=cfg.timeout_ms,
        fail_mode=cfg.fail_mode,
    )


# Convenience: fire-and-forget post event
def emit_post_async(sink: AuditSink, run: "ExecutionRun", result: Any) -> asyncio.Task:
    """Schedule post-run audit emission without blocking the caller."""
    return asyncio.create_task(sink.emit_post(run, result))
