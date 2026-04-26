"""Phase 0 P0.7 — LLM run.pre / run.post audit moved to BSGateway.

Lockin §Architectural shifts #1: BSNexus's ``audit_sink.py`` no longer
calls BSupervisor for **LLM run** pre/post events. BSGateway absorbs
that via its LiteLLM hook (BSGateway P0.7 PR), reading the metadata
that ``orchestrator_adapter.build_run_audit_metadata`` plumbs through
``litellm.acompletion(metadata=...)``.

What this PR retires
~~~~~~~~~~~~~~~~~~~~
* ``RunOrchestrator._dispatch_run_locked`` — no longer calls
  ``audit.preflight()`` or ``emit_post_async()``.
* ``RunOrchestrator.on_run_completed`` — no longer calls ``emit_post_async()``.
* ``dispatcher`` LLM-completion path — no longer resolves audit_sink.
* ``worker_result_consumer`` — no longer resolves audit_sink.

What survives
~~~~~~~~~~~~~
* ``BSupervisorAuditSink`` class itself — still useful for **non-LLM
  workflow / budget audit** (Lockin §Architectural shifts #1 explicit
  exception). Auth provider on those calls is service JWT (P0.7 swap).
* ``NoopAuditSink`` — same.

These regression tests pin the new shape. The integration test that
previously asserted "blocked-by-preflight short-circuits the executor"
(``test_audit_block_short_circuits_before_executor``) gets retired —
that path is now BSGateway's responsibility and BSNexus should not
short-circuit on its own.
"""

from __future__ import annotations

import ast
import inspect

from backend.src.core import dispatcher as dispatcher_module
from backend.src.core import run_orchestrator as run_orchestrator_module
from backend.src.queue import worker_result_consumer as worker_result_consumer_module


def _module_source(mod) -> str:
    """Return the full source of ``mod`` for line-level inspection."""
    return inspect.getsource(mod)


def _has_attr_call(tree: ast.AST, attr_name: str) -> bool:
    """True iff the AST contains any ``X.<attr_name>(...)`` call.

    Comments/docstrings are not parsed as calls, so this is the right
    primitive for "did the source actually call this method anymore".
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == attr_name:
                return True
    return False


def _has_named_call(tree: ast.AST, name: str) -> bool:
    """True iff the AST contains any ``<name>(...)`` call (free function)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == name:
                return True
    return False


def test_run_orchestrator_does_not_call_audit_preflight_anymore():
    """The LLM run.pre BSupervisor call has moved to BSGateway. The
    orchestrator MUST NOT call ``audit.preflight`` directly anymore —
    BSGateway's async_pre_call_hook handles it.
    """
    tree = ast.parse(_module_source(run_orchestrator_module))
    assert not _has_attr_call(tree, "preflight"), (
        "run_orchestrator MUST NOT call audit.preflight — BSGateway P0.7 "
        "absorbs the LLM run.pre check via its LiteLLM hook"
    )


def test_run_orchestrator_does_not_call_emit_post_async_anymore():
    """``emit_post_async`` (LLM run.post BSupervisor) has moved to
    BSGateway too. Pin no call sites left."""
    tree = ast.parse(_module_source(run_orchestrator_module))
    assert not _has_named_call(tree, "emit_post_async"), (
        "run_orchestrator MUST NOT call emit_post_async — BSGateway P0.7 "
        "absorbs LLM run.post via its LiteLLM async_post_call_hook"
    )


def test_dispatcher_does_not_resolve_audit_sink_in_llm_path_anymore():
    """The dispatcher's LLM-completion path no longer resolves an audit
    sink — BSGateway owns the run.post call."""
    tree = ast.parse(_module_source(dispatcher_module))
    assert not _has_named_call(tree, "resolve_audit_sink"), (
        "dispatcher MUST NOT resolve audit_sink for LLM completions — "
        "BSGateway P0.7 absorbs the run.post call via LiteLLM hook"
    )


def test_worker_result_consumer_does_not_resolve_audit_sink_anymore():
    """Worker-result consumer paths are also LLM-driven. No audit_sink
    calls — BSGateway owns it."""
    tree = ast.parse(_module_source(worker_result_consumer_module))
    assert not _has_named_call(tree, "resolve_audit_sink"), (
        "worker_result_consumer MUST NOT resolve audit_sink — BSGateway "
        "P0.7 absorbs LLM run.post for worker-completed runs as well"
    )


def test_audit_sink_module_survives_for_non_llm_workflow_audit():
    """The audit_sink module itself MUST still exist — it's the
    primitive used for non-LLM workflow/budget audit (Lockin
    §Architectural shifts #1 explicit exception). What's retired is
    the LLM call sites, not the module."""
    from backend.src.core.audit import (
        AuditResult,
        AuditSink,
        BSupervisorAuditSink,
        NoopAuditSink,
        emit_post_async,
        resolve_audit_sink,
    )

    # Public surface preserved for future non-LLM callers.
    assert AuditResult is not None
    assert AuditSink is not None
    assert BSupervisorAuditSink is not None
    assert NoopAuditSink is not None
    assert emit_post_async is not None
    assert resolve_audit_sink is not None


def test_audit_sink_resolve_uses_service_jwt_minter_when_provided():
    """``resolve_audit_sink`` accepts a ``service_jwt_minter`` kwarg —
    the P0.7 swap point that wires the new auth_provider closure into
    the underlying BaseServiceClient. With the minter, the auth_token
    static-api-key fallback is bypassed entirely.

    Pin: this is the single line that reroutes BSupervisor traffic
    from static api_key → service JWT for the non-LLM audit paths
    that survive in BSNexus.
    """
    from backend.src.core.audit import BSupervisorAuditSink, resolve_audit_sink
    from backend.src.core.integrations.config import AuditProviderConfig
    from backend.src.core.service_auth import ServiceJWTMinter

    minter = ServiceJWTMinter(
        bsvibe_auth_url="https://auth.bsvibe.dev",
        bootstrap_token_provider=lambda: "boot",
    )

    cfg = AuditProviderConfig(enabled=True, base_url="http://supervisor", api_key=None)

    # With minter present, no api_key + no auth_token still produces a
    # working sink (because the minter replaces both legacy auth paths).
    sink = resolve_audit_sink(
        cfg,
        auth_token=None,
        service_jwt_minter=minter,
        tenant_id="t1",
    )
    assert isinstance(sink, BSupervisorAuditSink), (
        "When a service_jwt_minter is provided, resolve_audit_sink must "
        "return a real sink — the minter replaces the static-api-key path"
    )


def test_resolve_knowledge_client_uses_service_jwt_minter_when_provided():
    """Mirror of the audit-sink minter wire-in for BSage/knowledge_client.
    The composer calls ``resolve_knowledge_client(snapshot.bsage,
    service_jwt_minter=...)`` and gets back a BSageKnowledgeClient whose
    auth_provider is the minted service JWT closure (not the static
    api_key).
    """
    from backend.src.core.composer import (
        BSageKnowledgeClient,
        resolve_knowledge_client,
    )
    from backend.src.core.integrations.config import ProviderConfig
    from backend.src.core.service_auth import ServiceJWTMinter

    minter = ServiceJWTMinter(
        bsvibe_auth_url="https://auth.bsvibe.dev",
        bootstrap_token_provider=lambda: "boot",
    )

    cfg = ProviderConfig(enabled=True, base_url="http://bsage", api_key=None)
    client = resolve_knowledge_client(
        cfg,
        service_jwt_minter=minter,
        tenant_id="t1",
    )
    assert isinstance(client, BSageKnowledgeClient)


def test_tenant_integration_config_api_key_is_no_longer_consulted_by_factory():
    """``TenantIntegrationConfig.api_key`` is being phased out (DB column
    stays for Phase A drop). When a service_jwt_minter is in play, the
    factory MUST NOT pass the api_key through to the adapter — the
    minter is the single source of auth."""
    from backend.src.core.audit import BSupervisorAuditSink, resolve_audit_sink
    from backend.src.core.integrations.config import AuditProviderConfig
    from backend.src.core.service_auth import ServiceJWTMinter

    minter = ServiceJWTMinter(
        bsvibe_auth_url="https://auth.bsvibe.dev",
        bootstrap_token_provider=lambda: "boot",
    )

    # Even when api_key is set in the DB row, the minter takes
    # precedence — _instance_token MUST be empty so the BaseServiceClient
    # closure is the only auth source.
    cfg = AuditProviderConfig(enabled=True, base_url="http://supervisor", api_key="legacy-static-key")
    sink = resolve_audit_sink(
        cfg,
        auth_token=None,
        service_jwt_minter=minter,
        tenant_id="t1",
    )
    assert isinstance(sink, BSupervisorAuditSink)
    # The legacy static api_key MUST NOT be in the headers snapshot —
    # the closure is what generates the Authorization header now.
    assert sink._instance_token == "", (
        "When service_jwt_minter is provided, the legacy static api_key "
        "MUST NOT be remembered by the adapter — the minter is the only "
        "auth source (Lockin §Architectural shifts #2)"
    )
