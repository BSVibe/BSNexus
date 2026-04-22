"""Conversation API — list + send messages, with RequestExtractor side effect."""

from __future__ import annotations

import pytest


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
async def test_send_chit_chat_does_not_create_request(client):
    project_id = await _make_project(client)

    resp = await client.post(
        f"/api/v1/projects/{project_id}/messages",
        json={"content": "hello"},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["intent"] == "chit_chat"
    assert body["request_id"] is None
    assert body["request_created"] is False
    assert body["message"]["role"] == "user"
    assert body["message"]["content"] == "hello"


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
async def test_send_modification_appends_to_open_request(client):
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

    assert second["intent"] == "modification"
    assert second["request_created"] is False
    assert second["request_id"] == first["request_id"]


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
    assert len(rows) == 2
    assert rows[0]["content"] == "hi"
    assert rows[1]["content"] == "Please build X"
    assert rows[1]["request_id"] is not None


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
