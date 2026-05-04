"""Run-audit metadata builder used by the dispatcher.

After the Direction reset 2026-05-03, the in-process LiteLLM tool-loop
adapter has been retired — :class:`backend.src.core.bsgateway.BSGatewayAdapter`
is the only executor adapter built by the dispatcher. The only piece
worth keeping from the old ``orchestrator_adapter`` module is the
audit-metadata factory below: it formats the BSupervisor wire contract
the BSGateway pre/post hooks consume on BSNexus's behalf.
"""

from __future__ import annotations

from typing import Any

from bsvibe_llm import RunAuditMetadata


def build_run_audit_metadata(
    *,
    run: Any,
    snapshot: Any | None,
) -> dict[str, Any]:
    """Build the ``metadata`` dict BSGateway's run audit hooks consume.

    Cross-PR contract (Lockin §Architectural shifts #1) — keys mirror
    what BSGateway forwards to BSupervisor as ``run.pre`` / ``run.post``
    events.

    Delegates to :class:`bsvibe_llm.RunAuditMetadata` — the canonical
    wire-format contract published by BSGateway PR #24 + BSNexus PR #38.
    Drift between BSNexus and BSGateway is now a ``bsvibe-llm``
    release-gate failure rather than a per-product regression test.

    Backwards-compat note: the legacy key ``cost_estimate`` is still
    populated alongside ``cost_estimate_cents`` so any consumer pinned
    to the older shape keeps reading a meaningful value while migrating.
    """
    request_id = getattr(run, "request_id", None)
    parent_run_id = getattr(run, "parent_run_id", None)
    agent_name = getattr(snapshot, "persona_label", None) if snapshot is not None else None
    cost_cents = getattr(snapshot, "cost_estimate_cents", None) if snapshot is not None else None

    metadata = RunAuditMetadata(
        tenant_id=str(run.tenant_id),
        run_id=str(run.id),
        request_id=str(request_id) if request_id is not None else None,
        parent_run_id=str(parent_run_id) if parent_run_id is not None else None,
        agent_name=agent_name,
        cost_estimate_cents=cost_cents,
    )
    out = metadata.to_metadata()
    # ``RunAuditMetadata.to_metadata()`` drops None values; BSGateway's
    # current parser tolerates that. Re-surface the keys BSNexus
    # historically emitted as ``None`` so call sites that ``in`` /
    # ``.get(...)`` on the result keep finding them (including the
    # legacy ``cost_estimate`` alias).
    out.setdefault("request_id", None)
    out.setdefault("parent_run_id", None)
    out.setdefault("agent_name", None)
    out.setdefault("cost_estimate_cents", None)
    out["cost_estimate"] = out["cost_estimate_cents"]
    return out
