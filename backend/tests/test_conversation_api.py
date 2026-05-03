"""Conversation API — list + send messages, with RequestExtractor side effect."""

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


@pytest.mark.asyncio
async def _make_project(client, name: str = "Test") -> str:
    resp = await client.post(
        "/api/v1/projects",
        json={"name": name},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_list_messages_empty(client):
    project_id = await _make_project(client)
    resp = await client.get(
        f"/api/v1/projects/{project_id}/messages",
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
        f"/api/v1/projects/{project_id}/messages",
        json={"content": "   "},
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
        f"/api/v1/projects/{project_id}/messages",
        json={"content": "hello"},
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
        f"/api/v1/projects/{project_id}/messages",
        json={"content": "Please implement the login screen"},
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
        f"/api/v1/projects/{project_id}/messages",
        json={"content": "Please implement the login screen"},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    request_id = uuid.UUID(body["request_id"])

    # background dispatcher was invoked exactly once with this run
    assert len(_stub_background_dispatch) == 1
    run_id, _tenant, called_project_id, _stream = _stub_background_dispatch[0]
    assert called_project_id == uuid.UUID(project_id)

    # and the run row actually exists, pending, linked to the new request
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
        f"/api/v1/projects/{project_id}/messages",
        json={"content": "Please implement the login screen"},
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

    # user message + ack
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
        f"/api/v1/projects/{project_id}/messages",
        json={"content": "   "},
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
        f"/api/v1/projects/{project_id}/messages",
        json={"content": "Please build the dashboard"},
        headers={"Authorization": "Bearer user-jwt-abc"},
    )
    assert resp.status_code == 201
    request_id = uuid.UUID(resp.json()["request_id"])

    row = (await db_session.execute(select(FounderRequest).where(FounderRequest.id == request_id))).scalar_one()
    assert row.originator_auth == "user-jwt-abc"


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
            f"/api/v1/projects/{project_id}/messages",
            json={"content": "Please implement the login screen"},
            headers={"Authorization": "Bearer fake"},
        )
    ).json()
    second = (
        await client.post(
            f"/api/v1/projects/{project_id}/messages",
            json={"content": "change it to use magic links"},
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
        f"/api/v1/projects/{project_id}/messages",
        json={"content": "  \n  "},
        headers={"Authorization": "Bearer fake"},
    )
    assert _stub_background_dispatch == []


@pytest.mark.asyncio
async def test_list_messages_returns_chronological(client):
    project_id = await _make_project(client)

    await client.post(
        f"/api/v1/projects/{project_id}/messages",
        json={"content": "hi"},
        headers={"Authorization": "Bearer fake"},
    )
    await client.post(
        f"/api/v1/projects/{project_id}/messages",
        json={"content": "Please build X"},
        headers={"Authorization": "Bearer fake"},
    )

    rows = (
        await client.get(
            f"/api/v1/projects/{project_id}/messages",
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
    # Each user msg gets its own Request now (no modification routing).
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
        f"/api/v1/projects/{foreign.id}/messages",
        json={"content": "hi"},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 404
