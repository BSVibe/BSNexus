"""Phase 0 P0.7 — orchestrator_adapter audit-metadata plumb.

Architectural shift #1 (Lockin §2): LLM ``run.pre`` / ``run.post`` events
move from BSNexus → BSGateway. BSGateway absorbs the BSupervisor LLM
precheck via its LiteLLM hook (BSGateway P0.7 PR).

For BSGateway to do that, BSNexus must pass run-audit info downstream.
The LiteLLM SDK supports a ``metadata`` kwarg that the proxy/gateway can
read in ``async_pre_call_hook`` / ``async_post_call_hook``. This test
file pins the metadata contract — keys that BSGateway P0.7 PR expects:

- ``tenant_id``       — UUID string of the tenant this run belongs to
- ``run_id``          — UUID string of this ExecutionRun
- ``request_id``      — UUID string of the parent Request (or None)
- ``parent_run_id``   — UUID string of the parent ExecutionRun (or None)
- ``agent_name``      — persona / agent label for observability grouping
- ``cost_estimate``   — pre-run cost estimate in USD cents (or None)

Drift between this file and BSGateway's metadata reader breaks audit —
hence the contract pinning.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.src.core.orchestrator_adapter import LiteLLMOrchestratorAdapter


def _fake_response(content: str = "ok"):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=None),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


@pytest.mark.asyncio
async def test_adapter_passes_run_audit_metadata_to_litellm():
    """When the adapter is constructed with run-audit info, it MUST
    forward those keys via ``litellm.acompletion(metadata=...)`` so
    BSGateway's pre/post hooks can read them."""
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    request_id = uuid.uuid4()

    adapter = LiteLLMOrchestratorAdapter(
        model="ollama/glm-4.7-flash:latest",
        project_id=project_id,
        api_key="unused",
        base_url="http://localhost:11434",
        run_audit_metadata={
            "tenant_id": str(tenant_id),
            "run_id": str(run_id),
            "request_id": str(request_id),
            "parent_run_id": None,
            "agent_name": "builder",
            "cost_estimate": 12,
        },
    )

    with (
        patch(
            "backend.src.core.orchestrator_adapter.litellm.acompletion",
            AsyncMock(return_value=_fake_response()),
        ) as mock_call,
        patch(
            "backend.src.core.orchestrator_adapter.litellm.cost_per_token",
            return_value=(0.0, 0.0),
        ),
    ):
        await adapter.execute(
            "system",
            "user",
            tools_allowed=[],
        )

    kwargs = mock_call.await_args.kwargs
    assert "metadata" in kwargs, (
        "litellm.acompletion MUST receive metadata kwarg so BSGateway "
        "can read run audit info in its async_pre_call_hook"
    )
    md = kwargs["metadata"]
    assert md["tenant_id"] == str(tenant_id)
    assert md["run_id"] == str(run_id)
    assert md["request_id"] == str(request_id)
    assert md["parent_run_id"] is None
    assert md["agent_name"] == "builder"
    assert md["cost_estimate"] == 12


@pytest.mark.asyncio
async def test_adapter_omits_metadata_when_run_audit_not_set():
    """Backwards compat — adapters built without run-audit metadata
    (legacy callers, tests) MUST still work. ``metadata`` kwarg is
    omitted entirely so litellm doesn't see an empty dict it might
    forward as a stray header."""
    adapter = LiteLLMOrchestratorAdapter(
        model="ollama/glm-4.7-flash:latest",
        project_id=uuid.uuid4(),
        api_key="unused",
    )

    with (
        patch(
            "backend.src.core.orchestrator_adapter.litellm.acompletion",
            AsyncMock(return_value=_fake_response()),
        ) as mock_call,
        patch(
            "backend.src.core.orchestrator_adapter.litellm.cost_per_token",
            return_value=(0.0, 0.0),
        ),
    ):
        await adapter.execute("s", "u", tools_allowed=[])

    kwargs = mock_call.await_args.kwargs
    assert "metadata" not in kwargs


@pytest.mark.asyncio
async def test_adapter_metadata_preserved_across_tool_loop_iterations():
    """Each iteration of the tool loop sends the same metadata payload —
    BSGateway's hook must be able to correlate every LLM hop with the
    original ExecutionRun even when tool-calls multiply turns."""
    run_id = uuid.uuid4()

    # First call: model returns a tool_call. Second call: returns final content.
    tool_call_response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            id="call_1",
                            type="function",
                            function=SimpleNamespace(
                                name="file_list",
                                arguments="{}",
                            ),
                        )
                    ],
                ),
                finish_reason="tool_calls",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )
    final_response = _fake_response("done")

    adapter = LiteLLMOrchestratorAdapter(
        model="ollama/glm-4.7-flash:latest",
        project_id=uuid.uuid4(),
        api_key="unused",
        run_audit_metadata={"tenant_id": "t1", "run_id": str(run_id), "agent_name": "a"},
    )

    call_metadatas: list[dict] = []

    async def acompletion_capture(**kwargs):
        call_metadatas.append(kwargs.get("metadata"))
        if len(call_metadatas) == 1:
            return tool_call_response
        return final_response

    with (
        patch(
            "backend.src.core.orchestrator_adapter.litellm.acompletion",
            new=acompletion_capture,
        ),
        patch(
            "backend.src.core.orchestrator_adapter.litellm.cost_per_token",
            return_value=(0.0, 0.0),
        ),
        patch(
            "backend.src.core.orchestrator_adapter.execute_tool_call",
            new=AsyncMock(return_value="[]"),
        ),
    ):
        await adapter.execute("s", "u", tools_allowed=["file_list"])

    assert len(call_metadatas) == 2
    assert call_metadatas[0] == call_metadatas[1]
    assert call_metadatas[0]["run_id"] == str(run_id)


@pytest.mark.asyncio
async def test_adapter_factory_allows_metadata_construction_from_run_object():
    """Build the metadata dict the way ``dispatcher`` will at runtime —
    from an ExecutionRun + its parent Request — and confirm the contract
    keys land. Pin: this is the cross-PR boundary with BSGateway."""

    run = SimpleNamespace(
        id=uuid.UUID("00000000-0000-0000-0000-000000000011"),
        tenant_id=uuid.UUID("00000000-0000-0000-0000-000000000012"),
        request_id=uuid.UUID("00000000-0000-0000-0000-000000000013"),
        parent_run_id=None,
    )
    snapshot = SimpleNamespace(
        persona_label="builder",
        cost_estimate_cents=15,
    )

    from backend.src.core.orchestrator_adapter import build_run_audit_metadata

    md = build_run_audit_metadata(run=run, snapshot=snapshot)
    assert md["tenant_id"] == "00000000-0000-0000-0000-000000000012"
    assert md["run_id"] == "00000000-0000-0000-0000-000000000011"
    assert md["request_id"] == "00000000-0000-0000-0000-000000000013"
    assert md["parent_run_id"] is None
    assert md["agent_name"] == "builder"
    assert md["cost_estimate"] == 15


@pytest.mark.asyncio
async def test_build_run_audit_metadata_handles_missing_optional_fields():
    """No request_id / no snapshot.persona_label / no cost_estimate
    must still produce a valid metadata dict (with None for absent
    keys). BSGateway's hook reads with defaults."""
    run = SimpleNamespace(
        id=uuid.UUID("00000000-0000-0000-0000-000000000011"),
        tenant_id=uuid.UUID("00000000-0000-0000-0000-000000000012"),
        request_id=None,
        parent_run_id=uuid.UUID("00000000-0000-0000-0000-000000000020"),
    )

    from backend.src.core.orchestrator_adapter import build_run_audit_metadata

    md = build_run_audit_metadata(run=run, snapshot=None)
    assert md["tenant_id"] == "00000000-0000-0000-0000-000000000012"
    assert md["run_id"] == "00000000-0000-0000-0000-000000000011"
    assert md["request_id"] is None
    assert md["parent_run_id"] == "00000000-0000-0000-0000-000000000020"
    assert md["agent_name"] is None
    assert md["cost_estimate"] is None


@pytest.mark.asyncio
async def test_build_run_audit_metadata_emits_canonical_cost_key():
    """Phase A Batch 5 — bsvibe-llm.RunAuditMetadata is the canonical
    wire-format contract. BSGateway PR #24's parser reads
    ``cost_estimate_cents`` (NOT the legacy ``cost_estimate``); this
    test pins that the dict ``build_run_audit_metadata`` returns now
    carries the canonical key alongside the legacy alias.

    Without this guard a future refactor that renames keys would
    silently break BSGateway → BSupervisor cost attribution.
    """
    run = SimpleNamespace(
        id=uuid.UUID("00000000-0000-0000-0000-000000000011"),
        tenant_id=uuid.UUID("00000000-0000-0000-0000-000000000012"),
        request_id=None,
        parent_run_id=None,
    )
    snapshot = SimpleNamespace(persona_label="builder", cost_estimate_cents=42)

    from backend.src.core.orchestrator_adapter import build_run_audit_metadata

    md = build_run_audit_metadata(run=run, snapshot=snapshot)
    # canonical bsvibe-llm key
    assert md["cost_estimate_cents"] == 42
    # legacy alias preserved for in-flight consumers during the migration
    assert md["cost_estimate"] == 42


@pytest.mark.asyncio
async def test_build_run_audit_metadata_round_trip_via_run_audit_metadata_dataclass():
    """The dict produced by ``build_run_audit_metadata`` must round-trip
    cleanly through ``RunAuditMetadata.from_metadata`` so BSGateway's
    parser (which uses the same dataclass on its side) reconstructs the
    same fields. This is the single contract pin for the producer side
    of the cross-PR audit metadata wire format."""
    from bsvibe_llm import RunAuditMetadata

    run = SimpleNamespace(
        id=uuid.UUID("00000000-0000-0000-0000-000000000011"),
        tenant_id=uuid.UUID("00000000-0000-0000-0000-000000000012"),
        request_id=uuid.UUID("00000000-0000-0000-0000-000000000013"),
        parent_run_id=None,
    )
    snapshot = SimpleNamespace(persona_label="builder", cost_estimate_cents=15)

    from backend.src.core.orchestrator_adapter import build_run_audit_metadata

    md = build_run_audit_metadata(run=run, snapshot=snapshot)
    parsed = RunAuditMetadata.from_metadata(md)

    assert parsed is not None
    assert parsed.tenant_id == "00000000-0000-0000-0000-000000000012"
    assert parsed.run_id == "00000000-0000-0000-0000-000000000011"
    assert parsed.request_id == "00000000-0000-0000-0000-000000000013"
    assert parsed.parent_run_id is None
    assert parsed.agent_name == "builder"
    assert parsed.cost_estimate_cents == 15
