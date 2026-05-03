"""Tests for ``BSGatewayAdapter.set_on_chunk`` — the streaming hook the
dispatcher wires into project_events for live Inside panel rendering."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from backend.src.core.bsgateway import BSGatewayAdapter


class _CapturingClient:
    def __init__(self) -> None:
        self.last_kwargs: dict[str, Any] | None = None

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        self.last_kwargs = kwargs
        return {"output_type": "text", "output_ref": "ok", "actual_cost_cents": 0, "finish_reason": "stop"}


@pytest.mark.asyncio
async def test_on_chunk_callback_passed_through_to_client() -> None:
    fake = _CapturingClient()
    adapter = BSGatewayAdapter(
        client=fake, model="claude_code", project_id=uuid.uuid4(), run_audit_metadata={}
    )

    received: list[str] = []

    async def cb(text: str) -> None:
        received.append(text)

    adapter.set_on_chunk(cb)
    await adapter.execute("s", "u", tools_allowed=[])

    assert (fake.last_kwargs or {}).get("on_chunk") is cb


@pytest.mark.asyncio
async def test_on_chunk_omitted_when_unset() -> None:
    fake = _CapturingClient()
    adapter = BSGatewayAdapter(
        client=fake, model="claude_code", project_id=uuid.uuid4(), run_audit_metadata={}
    )
    await adapter.execute("s", "u", tools_allowed=[])

    assert "on_chunk" not in (fake.last_kwargs or {})
