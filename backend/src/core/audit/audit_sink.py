"""AuditSink — optional BSupervisor integration.

BSupervisor's ``POST /api/events`` is already sync with sub-50ms rule
evaluation; BSNexus calls it directly:

- Pre-run: block with 200ms timeout. Fail-open on timeout/error (allow
  the run, log warning). Consistent with "BSupervisor가 없어도 동작해야
  한다" requirement.
- Post-run: fire-and-forget via ``asyncio.create_task``.

``NoopAuditSink`` is the fallback when BSupervisor is disabled for the
tenant — logs via structlog only, always allows.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

import httpx
import structlog

from backend.src.core.integrations.config import AuditProviderConfig

if TYPE_CHECKING:
    from backend.src.models.composition_snapshot import CompositionSnapshot
    from backend.src.models.execution_run import ExecutionRun

logger = structlog.get_logger(__name__)


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
    async def preflight(
        self, run: "ExecutionRun", snapshot: "CompositionSnapshot"
    ) -> AuditResult: ...

    async def emit_post(
        self, run: "ExecutionRun", result: Any
    ) -> None: ...


class NoopAuditSink:
    """Fallback when BSupervisor isn't configured. Always allow."""

    async def preflight(
        self, run: "ExecutionRun", snapshot: "CompositionSnapshot"
    ) -> AuditResult:
        logger.debug("audit_noop_preflight", run_id=str(run.id))
        return AuditResult(blocked=False)

    async def emit_post(self, run: "ExecutionRun", result: Any) -> None:
        logger.debug("audit_noop_post", run_id=str(run.id))


class BSupervisorAuditSink:
    """Calls existing BSupervisor POST /api/events.

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
        timeout_ms: int = 200,
        fail_mode: str = "open",
    ):
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_ms / 1000.0
        self._fail_mode = fail_mode
        self._headers: dict[str, str] = {}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"

    def _fail_result(self, reason: str) -> AuditResult:
        blocked = self._fail_mode == "closed"
        return AuditResult(blocked=blocked, reason=reason, degraded=True)

    async def preflight(
        self, run: "ExecutionRun", snapshot: "CompositionSnapshot"
    ) -> AuditResult:
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
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                resp = await client.post(
                    f"{self._base_url}/api/events",
                    json=payload,
                    headers=self._headers,
                )
                resp.raise_for_status()
                body = resp.json()
        except httpx.TimeoutException:
            logger.warning("bsupervisor_preflight_timeout", run_id=str(run.id))
            return self._fail_result("preflight timeout")
        except Exception as exc:
            logger.warning(
                "bsupervisor_preflight_failed", run_id=str(run.id), error=str(exc)
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
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                resp = await client.post(
                    f"{self._base_url}/api/events",
                    json=payload,
                    headers=self._headers,
                )
                resp.raise_for_status()
        except Exception as exc:
            logger.warning(
                "bsupervisor_post_failed", run_id=str(run.id), error=str(exc)
            )


def _summarize_result(result: Any) -> dict:
    if isinstance(result, dict):
        return {k: v for k, v in result.items() if k in {"status", "output_type", "error"}}
    return {"type": type(result).__name__}


def resolve_audit_sink(cfg: AuditProviderConfig | None) -> AuditSink:
    """Factory: return BSupervisor sink when configured, Noop otherwise."""
    if cfg is not None and cfg.enabled and cfg.base_url:
        return BSupervisorAuditSink(
            cfg.base_url,
            cfg.api_key,
            timeout_ms=cfg.timeout_ms,
            fail_mode=cfg.fail_mode,
        )
    return NoopAuditSink()


# Convenience: fire-and-forget post event
def emit_post_async(
    sink: AuditSink, run: "ExecutionRun", result: Any
) -> asyncio.Task:
    """Schedule post-run audit emission without blocking the caller."""
    return asyncio.create_task(sink.emit_post(run, result))
