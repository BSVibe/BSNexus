"""Tests for ``BSGatewayAdapter`` — orchestrator-facing wrapper around
``BSGatewayClient``.

Adapter mimics the ``LiteLLMOrchestratorAdapter`` surface
(``execute(system_prompt, user_prompt, *, tools_allowed, history)``)
so ``RunOrchestrator`` switches without changing call sites.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest

from backend.src.core.bsgateway import BSGatewayAdapter


class _FakeClient:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.last_call: dict[str, Any] | None = None

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        self.last_call = kwargs
        return self.response


@pytest.mark.asyncio
async def test_execute_translates_to_messages_payload() -> None:
    fake = _FakeClient({"output_type": "text", "output_ref": "ok", "actual_cost_cents": 0, "finish_reason": "stop"})
    adapter = BSGatewayAdapter(
        client=fake,
        model="claude_code",
        project_id=uuid.uuid4(),
        run_audit_metadata={"tenant_id": "t1", "run_id": "r1"},
        workspace_dir="/abs/ws",
    )

    result = await adapter.execute(
        "system brief",
        "do this",
        tools_allowed=["file_read"],
        history=[
            {"role": "user", "content": "earlier"},
            {"role": "assistant", "content": "earlier reply"},
        ],
    )

    assert result["output_type"] == "text"
    assert result["output_ref"] == "ok"
    call = fake.last_call or {}
    assert call["model"] == "claude_code"
    assert call["messages"][0] == {"role": "system", "content": "system brief"}
    assert call["messages"][1] == {"role": "user", "content": "earlier"}
    assert call["messages"][2] == {"role": "assistant", "content": "earlier reply"}
    assert call["messages"][-1] == {"role": "user", "content": "do this"}
    assert call["metadata"]["tenant_id"] == "t1"
    assert call["workspace_dir"] == "/abs/ws"


@pytest.mark.asyncio
async def test_set_run_audit_metadata_overrides_initial() -> None:
    fake = _FakeClient({"output_type": "text", "output_ref": "ok", "actual_cost_cents": 0, "finish_reason": "stop"})
    adapter = BSGatewayAdapter(client=fake, model="claude_code", project_id=uuid.uuid4(), run_audit_metadata={"tenant_id": "t1"})

    adapter.set_run_audit_metadata({"tenant_id": "t1", "run_id": "r2"})
    await adapter.execute("s", "u", tools_allowed=[])

    assert (fake.last_call or {})["metadata"] == {"tenant_id": "t1", "run_id": "r2"}


@pytest.mark.asyncio
async def test_history_filters_unknown_roles() -> None:
    fake = _FakeClient({"output_type": "text", "output_ref": "", "actual_cost_cents": 0, "finish_reason": "stop"})
    adapter = BSGatewayAdapter(client=fake, model="claude_code", project_id=uuid.uuid4(), run_audit_metadata={})

    await adapter.execute(
        "s",
        "u",
        tools_allowed=[],
        history=[
            {"role": "system", "content": "drop me"},
            {"role": "user", "content": "keep"},
            {"role": "tool", "content": "drop"},
            {"role": "assistant", "content": "keep"},
            {"role": "user", "content": ""},  # empty content also dropped
        ],
    )

    msgs = (fake.last_call or {})["messages"]
    # 1 system + 2 history (user "keep", assistant "keep") + 1 user prompt
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    assert [m["content"] for m in msgs] == ["s", "keep", "keep", "u"]


@pytest.mark.asyncio
async def test_blocked_run_propagates_bsgateway_error() -> None:
    """BSGatewayError from the client surfaces — orchestrator catches and goes to blocked."""
    from backend.src.core.bsgateway import BSGatewayError

    class _BoomClient:
        async def execute(self, **_kwargs: Any) -> dict[str, Any]:
            raise BSGatewayError("worker died")

    adapter = BSGatewayAdapter(client=_BoomClient(), model="claude_code", project_id=uuid.uuid4(), run_audit_metadata={})

    with pytest.raises(BSGatewayError, match="worker died"):
        await adapter.execute("s", "u", tools_allowed=[])


@pytest.mark.asyncio
async def test_set_workspace_dir_updates_subsequent_calls() -> None:
    fake = _FakeClient({"output_type": "text", "output_ref": "", "actual_cost_cents": 0, "finish_reason": "stop"})
    adapter = BSGatewayAdapter(client=fake, model="claude_code", project_id=uuid.uuid4(), run_audit_metadata={})

    adapter.set_workspace_dir("/new/ws")
    adapter.set_mcp_servers({"bsnexus": {"url": "http://x", "headers": {}}})
    await adapter.execute("s", "u", tools_allowed=[])

    call = fake.last_call or {}
    assert call["workspace_dir"] == "/new/ws"
    assert call["mcp_servers"] == {"bsnexus": {"url": "http://x", "headers": {}}}
