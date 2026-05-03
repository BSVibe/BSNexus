"""Tests for ``core.bsgateway.BSGatewayClient`` — the httpx wrapper that
replaces ``LiteLLMOrchestratorAdapter`` for executor-model dispatch.

The client posts to BSGateway's ``/api/v1/chat/completions`` and
consumes the streaming SSE response. It exposes the same return shape
that ``RunOrchestrator`` already records (``output_type``, ``output_ref``,
``actual_cost_cents``) so the orchestrator switch is non-disruptive.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from backend.src.core.bsgateway import BSGatewayClient, BSGatewayError


def _sse_lines(*chunks: dict[str, Any]) -> list[str]:
    """Build a list of SSE data lines for an iter_lines() mock."""
    out: list[str] = []
    for c in chunks:
        out.append(f"data: {json.dumps(c)}")
        out.append("")
    out.append("data: [DONE]")
    out.append("")
    return out


class _FakeStreamResp:
    def __init__(self, lines: list[str], status_code: int = 200) -> None:
        self.status_code = status_code
        self._lines = lines

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=MagicMock(), response=MagicMock(status_code=self.status_code))

    async def aiter_lines(self) -> AsyncIterator[str]:
        for line in self._lines:
            yield line


class _FakeStreamCtx:
    def __init__(self, resp: _FakeStreamResp) -> None:
        self._resp = resp

    async def __aenter__(self) -> _FakeStreamResp:
        return self._resp

    async def __aexit__(self, *exc: Any) -> None:
        return None


class _FakeClient:
    def __init__(self, lines: list[str], status_code: int = 200) -> None:
        self._lines = lines
        self._status = status_code
        self.last_headers: dict[str, str] | None = None
        self.last_url: str | None = None
        self.last_payload: dict[str, Any] | None = None

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    def stream(self, method: str, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> _FakeStreamCtx:
        assert method == "POST"
        self.last_url = url
        self.last_headers = headers
        self.last_payload = json
        return _FakeStreamCtx(_FakeStreamResp(self._lines, self._status))


# ─── basic streaming aggregation ─────────────────────────────────────


@pytest.mark.asyncio
async def test_stream_chunks_aggregate_into_output() -> None:
    """delta.content fragments concatenate; finish_reason terminates."""
    client = BSGatewayClient(base_url="https://gw.test", api_key="k")
    lines = _sse_lines(
        {"choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"content": "Hello "}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"content": "world"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    )
    fake = _FakeClient(lines)

    with patch("backend.src.core.bsgateway.client.httpx.AsyncClient", return_value=fake):
        result = await client.execute(
            messages=[
                {"role": "system", "content": "brief"},
                {"role": "user", "content": "go"},
            ],
            metadata={"tenant_id": str(uuid.uuid4()), "run_id": str(uuid.uuid4())},
            model="claude_code",
        )

    assert result["output_type"] == "text"
    assert result["output_ref"] == "Hello world"
    assert result["finish_reason"] == "stop"


# ─── request shape ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_request_includes_workspace_dir_and_mcp_servers_in_metadata() -> None:
    client = BSGatewayClient(base_url="https://gw.test", api_key="k")
    fake = _FakeClient(_sse_lines({"choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}]}))

    mcp = {"bsnexus": {"url": "http://x/mcp/sse?token=t", "headers": {}}}

    with patch("backend.src.core.bsgateway.client.httpx.AsyncClient", return_value=fake):
        await client.execute(
            messages=[{"role": "user", "content": "go"}],
            metadata={"tenant_id": str(uuid.uuid4())},
            model="claude_code",
            workspace_dir="/abs/ws",
            mcp_servers=mcp,
        )

    payload = fake.last_payload or {}
    assert payload["model"] == "claude_code"
    assert payload["stream"] is True
    md = payload["metadata"]
    assert md["workspace_dir"] == "/abs/ws"
    assert md["mcp_servers"] == mcp


@pytest.mark.asyncio
async def test_omit_mcp_servers_when_none() -> None:
    """None / empty mcp_servers ⇒ key absent from metadata (back-compat)."""
    client = BSGatewayClient(base_url="https://gw.test", api_key="k")
    fake = _FakeClient(_sse_lines({"choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}]}))

    with patch("backend.src.core.bsgateway.client.httpx.AsyncClient", return_value=fake):
        await client.execute(
            messages=[{"role": "user", "content": "go"}],
            metadata={"tenant_id": str(uuid.uuid4())},
            model="claude_code",
        )

    md = (fake.last_payload or {})["metadata"]
    assert "mcp_servers" not in md


@pytest.mark.asyncio
async def test_authorization_header_uses_api_key() -> None:
    client = BSGatewayClient(base_url="https://gw.test", api_key="sk-xyz")
    fake = _FakeClient(_sse_lines({"choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}]}))

    with patch("backend.src.core.bsgateway.client.httpx.AsyncClient", return_value=fake):
        await client.execute(
            messages=[{"role": "user", "content": "go"}],
            metadata={"tenant_id": str(uuid.uuid4())},
            model="claude_code",
        )

    headers = fake.last_headers or {}
    assert headers.get("Authorization") == "Bearer sk-xyz"


# ─── error chunk handling ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_terminal_error_chunk_raises_bsgateway_error() -> None:
    client = BSGatewayClient(base_url="https://gw.test", api_key="k")
    lines = _sse_lines(
        {"choices": [{"index": 0, "delta": {"content": "partial"}, "finish_reason": None}]},
        {
            "choices": [],
            "error": {"message": "claude died", "type": "executor_error", "code": "executor_failed"},
        },
    )
    fake = _FakeClient(lines)

    with patch("backend.src.core.bsgateway.client.httpx.AsyncClient", return_value=fake):
        with pytest.raises(BSGatewayError, match="claude died"):
            await client.execute(
                messages=[{"role": "user", "content": "go"}],
                metadata={"tenant_id": str(uuid.uuid4())},
                model="claude_code",
            )


@pytest.mark.asyncio
async def test_http_error_raises_bsgateway_error() -> None:
    client = BSGatewayClient(base_url="https://gw.test", api_key="k")
    fake = _FakeClient([], status_code=503)

    with patch("backend.src.core.bsgateway.client.httpx.AsyncClient", return_value=fake):
        with pytest.raises(BSGatewayError):
            await client.execute(
                messages=[{"role": "user", "content": "go"}],
                metadata={"tenant_id": str(uuid.uuid4())},
                model="claude_code",
            )


# ─── chunk callback (PR3 will hook Inside panel here) ────────────────


@pytest.mark.asyncio
async def test_on_chunk_callback_receives_each_delta() -> None:
    """Callers can stream partial text to project_events SSE in real time."""
    client = BSGatewayClient(base_url="https://gw.test", api_key="k")
    lines = _sse_lines(
        {"choices": [{"index": 0, "delta": {"content": "Hi "}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"content": "there"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    )
    fake = _FakeClient(lines)

    received: list[str] = []

    async def on_chunk(text: str) -> None:
        received.append(text)

    with patch("backend.src.core.bsgateway.client.httpx.AsyncClient", return_value=fake):
        await client.execute(
            messages=[{"role": "user", "content": "go"}],
            metadata={"tenant_id": str(uuid.uuid4())},
            model="claude_code",
            on_chunk=on_chunk,
        )

    assert received == ["Hi ", "there"]
