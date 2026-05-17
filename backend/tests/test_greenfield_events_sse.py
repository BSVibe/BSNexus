"""Tests for the greenfield SSE endpoint (G7.2).

Endpoint: ``GET /api/v1/events?project_id=…&token=…``. Fans the
tenant-scoped Redis Stream ``project:events:{project_id}`` onto a
``text/event-stream`` response. Frontend consumer is
``frontend/src/hooks/useProjectEvents`` (EventSource auto-reconnect
honoring ``retry: 5000``).

The HTTP integration is exercised at the headers + 404/401 layer; the
event-stream wire format and the round-trip from the published event
to the SSE block are tested at the generator-coroutine layer because
the ASGI test transport doesn't propagate ``http.disconnect`` reliably
to ``request.is_disconnected()``, and an SSE response is by design an
infinite stream.
"""

from __future__ import annotations

import json
import uuid
from typing import AsyncIterator

import httpx
import pytest

from backend.src.api.events import _event_generator, _format_sse
from backend.src.models import Project


async def _make_project(db_session, tenant_id: uuid.UUID, name: str = "G7.2 SSE") -> Project:
    project = Project(tenant_id=tenant_id, name=name, description="")
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)
    return project


def test_format_sse_emits_event_and_data_lines():
    block = _format_sse("decision_resolved", {"id": "dec-1", "resolution": "ship"})
    # Each block ends with the SSE separator: blank line.
    assert block.endswith("\n\n")
    lines = block.rstrip().split("\n")
    assert lines[0] == "event: decision_resolved"
    assert lines[1].startswith("data: ")
    payload = json.loads(lines[1].removeprefix("data: "))
    assert payload == {"id": "dec-1", "resolution": "ship"}


def test_format_sse_handles_empty_data():
    block = _format_sse("ready")
    assert "event: ready" in block
    assert "data: {}" in block


# ─── Generator-coroutine round-trip ───────────────────────────────────


class _FakeRequest:
    """ASGI ``Request`` stub for generator tests. Flips
    ``is_disconnected`` to True after ``disconnect_after`` calls so
    the SSE generator terminates cleanly."""

    def __init__(self, disconnect_after: int = 3) -> None:
        self.disconnect_after = disconnect_after
        self.calls = 0

    async def is_disconnected(self) -> bool:
        self.calls += 1
        return self.calls > self.disconnect_after


class _FakeStreamManager:
    """Returns a queue of message-list batches per ``tail`` call.
    The last entry is consumed once and subsequent calls return [].
    """

    def __init__(self, batches: list[list[dict]]) -> None:
        self._batches = list(batches)

    async def tail(self, stream: str, last_id: str = "$", block: int = 15000) -> list[dict]:
        if self._batches:
            return self._batches.pop(0)
        return []


async def _collect(gen: AsyncIterator[str], limit: int = 20) -> list[str]:
    out: list[str] = []
    async for chunk in gen:
        out.append(chunk)
        if len(out) >= limit:
            break
    return out


@pytest.mark.asyncio
async def test_generator_emits_retry_then_ready_then_heartbeat():
    """First two blocks fix the wire contract — ``retry:`` directive
    and ``event: ready``. ``heartbeat`` lands on every empty tail."""
    request = _FakeRequest(disconnect_after=2)
    manager = _FakeStreamManager(batches=[])
    project_id = uuid.uuid4()

    blocks = await _collect(
        _event_generator(request, "project:events:test", manager, project_id),
        limit=10,
    )

    assert blocks[0] == "retry: 5000\n\n"
    assert blocks[1].startswith("event: ready\n")
    ready_payload = json.loads(blocks[1].split("data: ", 1)[1].strip())
    assert ready_payload == {"project_id": str(project_id)}
    # Subsequent blocks are heartbeats while no domain events arrive.
    assert any(b.startswith("event: heartbeat") for b in blocks[2:])


@pytest.mark.asyncio
async def test_generator_delivers_published_event():
    """A published ``decision_resolved`` event lands on the wire as a
    framed SSE block, with the original payload preserved verbatim."""
    request = _FakeRequest(disconnect_after=3)
    manager = _FakeStreamManager(
        batches=[
            [
                {
                    "_message_id": "1-0",
                    "event": "decision_resolved",
                    "data": {"id": "dec-123", "resolution": "ship"},
                }
            ]
        ]
    )
    project_id = uuid.uuid4()

    blocks = await _collect(
        _event_generator(request, "project:events:test", manager, project_id),
        limit=10,
    )

    found = [b for b in blocks if b.startswith("event: decision_resolved")]
    assert len(found) == 1, f"expected one decision_resolved block, got {blocks}"
    payload = json.loads(found[0].split("data: ", 1)[1].strip())
    assert payload == {"id": "dec-123", "resolution": "ship"}


@pytest.mark.asyncio
async def test_generator_advances_last_id_so_redelivery_does_not_loop():
    """Multiple events over multiple tails — the generator advances
    ``last_id`` so the same ``_message_id`` is never replayed."""
    request = _FakeRequest(disconnect_after=4)
    manager = _FakeStreamManager(
        batches=[
            [{"_message_id": "1-0", "event": "deliverable", "data": {"id": "d1"}}],
            [{"_message_id": "2-0", "event": "decision", "data": {"id": "dec-1"}}],
        ]
    )
    project_id = uuid.uuid4()

    blocks = await _collect(
        _event_generator(request, "project:events:test", manager, project_id),
        limit=15,
    )

    delivered = [b.split("\n")[0] for b in blocks if b.startswith("event: ")]
    # ready, then deliverable, then decision (heartbeats interleaved
    # but the domain-event blocks land in publish order).
    domain = [b for b in delivered if b not in ("event: ready", "event: heartbeat")]
    assert domain == ["event: deliverable", "event: decision"]


# ─── HTTP-layer contract: headers, 404, 401 ───────────────────────────


@pytest.mark.asyncio
async def test_events_endpoint_404s_cross_tenant_project(client, db_session, mock_tenant_id, seeded_tenant):
    from backend.src.models import Tenant

    other_tenant_id = uuid.uuid4()
    db_session.add(
        Tenant(
            id=other_tenant_id,
            name="Other",
            slug=f"other-{other_tenant_id.hex[:8]}",
            owner_user_id="other-user",
        )
    )
    await db_session.commit()
    other_project = await _make_project(db_session, other_tenant_id, "OtherProj")

    resp = await client.get(
        f"/api/v1/events?project_id={other_project.id}",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 404, resp.text


# ─── Publisher wiring ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_decision_publishes_decision_resolved_event(
    client, db_session, mock_tenant_id, mock_stream_manager, seeded_tenant
):
    """Resolving a decision via ``POST /api/v1/decisions/{id}/resolve``
    must publish a ``decision_resolved`` event onto the project's
    SSE stream so the founder's other tabs dismiss the row without a
    manual refresh."""
    from backend.src.models import Decision

    project = await _make_project(db_session, mock_tenant_id, "Decision Publisher")
    decision = Decision(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        question="How should the stalled work move forward?",
        options=["retry", "reframe"],
        blocking=True,
    )
    db_session.add(decision)
    await db_session.commit()

    resp = await client.post(
        f"/api/v1/decisions/{decision.id}/resolve",
        json={"resolution": "retry", "resolved_by": "founder@test"},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200, resp.text

    mock_stream_manager.publish_project_event.assert_called_once()
    call = mock_stream_manager.publish_project_event.call_args
    assert call.kwargs.get("project_id", call.args[0] if call.args else None) == str(project.id)
    # Allow either kwargs or positional shape — the publisher contract
    # is ``publish_project_event(project_id, event, data)``.
    args = call.args
    assert args[1] == "decision_resolved"
    assert args[2]["id"] == str(decision.id)
    assert args[2]["resolution"] == "retry"


@pytest.mark.asyncio
async def test_events_endpoint_requires_auth(test_app, db_session, mock_tenant_id, mock_stream_manager, seeded_tenant):
    """EventSource cannot send Authorization headers; the endpoint must
    accept ``?token=`` (handled upstream by ``get_current_user``) and
    reject anonymous opens with 401."""
    project = await _make_project(db_session, mock_tenant_id)

    test_app.state.stream_manager = mock_stream_manager
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=test_app),
        base_url="http://test",
    ) as anon_client:
        resp = await anon_client.get(f"/api/v1/events?project_id={project.id}")
        assert resp.status_code == 401, resp.text
