"""Conversation API — list + send messages, with RequestExtractor side effect.

Endpoints (decision-locks A3 flat shape):

- ``GET  /api/v1/messages?project_id={id}``
- ``POST /api/v1/messages`` with ``project_id`` in the body.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
import pytest_asyncio


@pytest_asyncio.fixture(autouse=True)
async def _stub_background_dispatch(monkeypatch):
    """Replace the orchestrator dispatcher so tests don't fire real LLMs."""
    calls: list[tuple[uuid.UUID, uuid.UUID, uuid.UUID, object]] = []

    async def _noop(run_id, tenant_id, project_id, stream_manager):
        calls.append((run_id, tenant_id, project_id, stream_manager))
        await asyncio.sleep(0)

    monkeypatch.setattr(
        "backend.src.api.conversation._BACKGROUND_DISPATCH",
        _noop,
    )
    return calls


async def _make_project(client, name: str = "Test") -> str:
    resp = await client.post(
        "/api/v1/projects",
        json={"name": name},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def _send(content: str, project_id: str) -> dict:
    return {"content": content, "project_id": project_id}


@pytest.mark.asyncio
async def test_list_messages_empty(client):
    project_id = await _make_project(client)
    resp = await client.get(
        f"/api/v1/messages?project_id={project_id}",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_send_empty_content_does_not_create_request(client):
    """Direction reset 2026-05-03: only blank/whitespace content skips
    Request creation. The chit_chat / question / modification classifier
    is retired."""
    project_id = await _make_project(client)

    resp = await client.post(
        "/api/v1/messages",
        json=_send("   ", project_id),
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["intent"] == "chit_chat"
    assert body["request_id"] is None
    assert body["request_created"] is False
    assert body["message"]["role"] == "user"
    assert body["message"]["content"] == "   "


@pytest.mark.asyncio
async def test_send_short_content_creates_request(client):
    """Even one-word user content opens a Request — there's no
    classifier to filter chit_chat anymore."""
    project_id = await _make_project(client)

    resp = await client.post(
        "/api/v1/messages",
        json=_send("hello", project_id),
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["intent"] == "request"
    assert body["request_id"] is not None
    assert body["request_created"] is True


@pytest.mark.asyncio
async def test_send_request_creates_request_and_links_message(client):
    project_id = await _make_project(client)

    resp = await client.post(
        "/api/v1/messages",
        json=_send("Please implement the login screen", project_id),
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["intent"] == "request"
    assert body["request_created"] is True
    assert body["request_id"] is not None
    assert body["message"]["request_id"] == body["request_id"]


@pytest.mark.asyncio
async def test_send_request_seeds_top_level_run_and_dispatches(client, db_session, _stub_background_dispatch):
    from backend.src.models import ExecutionRun

    project_id = await _make_project(client)

    resp = await client.post(
        "/api/v1/messages",
        json=_send("Please implement the login screen", project_id),
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    request_id = uuid.UUID(body["request_id"])

    assert len(_stub_background_dispatch) == 1
    run_id, _tenant, called_project_id, _stream = _stub_background_dispatch[0]
    assert called_project_id == uuid.UUID(project_id)

    from sqlalchemy import select

    row = (await db_session.execute(select(ExecutionRun).where(ExecutionRun.id == run_id))).scalar_one()
    assert row.request_id == request_id
    assert row.status.value == "pending"
    assert row.project_id == uuid.UUID(project_id)


@pytest.mark.asyncio
async def test_send_request_writes_immediate_ack_message(client, db_session, _stub_background_dispatch):
    """Founder shouldn't see a frozen chat while the run executes — an
    ack assistant message is persisted synchronously before the HTTP
    response returns."""
    from sqlalchemy import select

    from backend.src.models import ConversationMessage

    project_id = await _make_project(client)
    resp = await client.post(
        "/api/v1/messages",
        json=_send("Please implement the login screen", project_id),
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 201, resp.text
    request_id = uuid.UUID(resp.json()["request_id"])

    rows = (
        (
            await db_session.execute(
                select(ConversationMessage)
                .where(ConversationMessage.project_id == uuid.UUID(project_id))
                .order_by(ConversationMessage.created_at.asc())
            )
        )
        .scalars()
        .all()
    )

    assert len(rows) == 2
    assert rows[0].role == "user"
    ack = rows[1]
    assert ack.role == "assistant"
    assert ack.request_id == request_id
    assert "Starting" in ack.content
    assert any(isinstance(a, dict) and a.get("kind") == "ack" for a in (ack.actions or []))


@pytest.mark.asyncio
async def test_send_blank_content_does_not_write_ack(client, db_session, _stub_background_dispatch):
    """Whitespace-only content skips Request and run dispatch — no ack."""
    from sqlalchemy import select

    from backend.src.models import ConversationMessage

    project_id = await _make_project(client)
    resp = await client.post(
        "/api/v1/messages",
        json=_send("   ", project_id),
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 201, resp.text

    rows = (
        (
            await db_session.execute(
                select(ConversationMessage).where(ConversationMessage.project_id == uuid.UUID(project_id))
            )
        )
        .scalars()
        .all()
    )
    assert [r.role for r in rows] == ["user"]


@pytest.mark.asyncio
async def test_send_request_captures_originator_auth(client, db_session):
    from sqlalchemy import select

    from backend.src.models import Request as FounderRequest

    project_id = await _make_project(client)
    resp = await client.post(
        "/api/v1/messages",
        json=_send("Please build the dashboard", project_id),
        headers={"Authorization": "Bearer user-jwt-abc"},
    )
    assert resp.status_code == 201
    request_id = uuid.UUID(resp.json()["request_id"])

    row = (await db_session.execute(select(FounderRequest).where(FounderRequest.id == request_id))).scalar_one()
    assert row.originator_auth == "user-jwt-abc"


@pytest.mark.asyncio
async def test_send_request_emits_nexus_request_created_audit(client, db_session, mock_tenant_id):
    """Direction reset 2026-05-03 — the inline rule that replaced
    RequestExtractor must still emit ``nexus.request.created`` to the
    audit outbox so downstream services see the founder's intent."""
    from bsvibe_audit import AuditOutboxRecord
    from sqlalchemy import select

    project_id = await _make_project(client)
    resp = await client.post(
        "/api/v1/messages",
        json=_send("Implement the login screen", project_id),
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 201
    request_id = uuid.UUID(resp.json()["request_id"])

    rows = (await db_session.execute(select(AuditOutboxRecord))).scalars().all()
    request_rows = [r for r in rows if r.event_type == "nexus.request.created"]
    assert len(request_rows) == 1
    payload = request_rows[0].payload
    assert payload["actor"]["type"] == "user"
    assert payload["tenant_id"] == str(mock_tenant_id)
    assert payload["resource"]["type"] == "request"
    assert payload["resource"]["id"] == str(request_id)
    assert payload["data"]["intent_summary"] == "Implement the login screen"


@pytest.mark.asyncio
async def test_send_blank_content_does_not_emit_audit(client, db_session):
    """Whitespace-only content takes the no-Request branch — no audit
    emit, no outbox row."""
    from bsvibe_audit import AuditOutboxRecord
    from sqlalchemy import select

    project_id = await _make_project(client)
    await client.post(
        "/api/v1/messages",
        json=_send("   ", project_id),
        headers={"Authorization": "Bearer fake"},
    )

    rows = (await db_session.execute(select(AuditOutboxRecord))).scalars().all()
    types = [r.event_type for r in rows]
    assert "nexus.request.created" not in types


@pytest.mark.asyncio
async def test_each_user_message_creates_its_own_request(client, _stub_background_dispatch):
    """Direction reset 2026-05-03: every non-empty user message opens
    a fresh Request and seeds a fresh ExecutionRun. The previous
    modification-routing UX (append to in-flight Request) is retired —
    BSGateway's CLI agent reads chat history each turn, so the steer
    lands automatically without needing in-band routing."""
    project_id = await _make_project(client)

    first = (
        await client.post(
            "/api/v1/messages",
            json=_send("Please implement the login screen", project_id),
            headers={"Authorization": "Bearer fake"},
        )
    ).json()
    second = (
        await client.post(
            "/api/v1/messages",
            json=_send("change it to use magic links", project_id),
            headers={"Authorization": "Bearer fake"},
        )
    ).json()

    assert first["request_id"] != second["request_id"]
    assert second["request_created"] is True
    assert len(_stub_background_dispatch) == 2


@pytest.mark.asyncio
async def test_send_blank_content_does_not_seed_run(client, _stub_background_dispatch):
    project_id = await _make_project(client)

    await client.post(
        "/api/v1/messages",
        json=_send("  \n  ", project_id),
        headers={"Authorization": "Bearer fake"},
    )
    assert _stub_background_dispatch == []


@pytest.mark.asyncio
async def test_list_messages_returns_chronological(client):
    project_id = await _make_project(client)

    await client.post(
        "/api/v1/messages",
        json=_send("hi", project_id),
        headers={"Authorization": "Bearer fake"},
    )
    await client.post(
        "/api/v1/messages",
        json=_send("Please build X", project_id),
        headers={"Authorization": "Bearer fake"},
    )

    rows = (
        await client.get(
            f"/api/v1/messages?project_id={project_id}",
            headers={"Authorization": "Bearer fake"},
        )
    ).json()
    # Direction reset 2026-05-03: every non-empty user message creates a
    # Request and synthesises an ack. So 2 user msgs ⇒ 4 total.
    assert len(rows) == 4
    assert [r["role"] for r in rows] == ["user", "assistant", "user", "assistant"]
    assert rows[0]["content"] == "hi"
    assert rows[2]["content"] == "Please build X"
    assert rows[0]["request_id"] is not None
    assert rows[1]["request_id"] == rows[0]["request_id"]
    assert rows[2]["request_id"] is not None
    assert rows[3]["request_id"] == rows[2]["request_id"]
    assert rows[0]["request_id"] != rows[2]["request_id"]


@pytest.mark.asyncio
async def test_send_message_404_for_foreign_project(client, db_session):
    import uuid

    from backend.src.models import Project, Tenant

    other_tenant = Tenant(
        id=uuid.uuid4(),
        name="O",
        slug=f"o-{uuid.uuid4().hex[:8]}",
        owner_user_id="owner",
    )
    db_session.add(other_tenant)
    await db_session.commit()
    foreign = Project(tenant_id=other_tenant.id, name="Hidden", description="")
    db_session.add(foreign)
    await db_session.commit()
    await db_session.refresh(foreign)

    resp = await client.post(
        "/api/v1/messages",
        json={"content": "hi", "project_id": str(foreign.id)},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 404
