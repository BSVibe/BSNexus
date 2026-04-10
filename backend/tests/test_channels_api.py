"""Tests for the project channels CRUD + Slack adapter."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from backend.src.core.channel_adapter import (
    ChannelTarget,
    SlackChannelAdapter,
)
from backend.src.models import Project, ProjectStatus


async def _make_project(db_session) -> Project:
    now = datetime.now(timezone.utc)
    project = Project(
        id=uuid.uuid4(),
        name="Channel Project",
        description="",
        status=ProjectStatus.active,
        created_at=now,
        updated_at=now,
    )
    db_session.add(project)
    await db_session.commit()
    return project


# ── REST endpoints ──────────────────────────────────────────────────


async def test_create_channel(client: AsyncClient, db_session):
    project = await _make_project(db_session)
    resp = await client.post(
        f"/api/v1/projects/{project.id}/channels",
        json={
            "kind": "slack",
            "external_channel_id": "C12345",
            "display_name": "#general",
            "credentials": {"bot_token": "xoxb-fake"},
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["kind"] == "slack"
    assert body["external_channel_id"] == "C12345"
    assert body["is_active"] is True


async def test_create_channel_404_for_missing_project(client: AsyncClient):
    resp = await client.post(
        f"/api/v1/projects/{uuid.uuid4()}/channels",
        json={"kind": "slack", "external_channel_id": "C1"},
    )
    assert resp.status_code == 404


async def test_list_channels_returns_only_project_channels(client: AsyncClient, db_session):
    project_a = await _make_project(db_session)
    project_b = await _make_project(db_session)
    await client.post(
        f"/api/v1/projects/{project_a.id}/channels",
        json={"kind": "slack", "external_channel_id": "A"},
    )
    await client.post(
        f"/api/v1/projects/{project_b.id}/channels",
        json={"kind": "slack", "external_channel_id": "B"},
    )
    resp = await client.get(f"/api/v1/projects/{project_a.id}/channels")
    ids = [c["external_channel_id"] for c in resp.json()]
    assert ids == ["A"]


async def test_patch_channel(client: AsyncClient, db_session):
    project = await _make_project(db_session)
    create = await client.post(
        f"/api/v1/projects/{project.id}/channels",
        json={"kind": "slack", "external_channel_id": "C1", "display_name": "old"},
    )
    channel_id = create.json()["id"]
    resp = await client.patch(
        f"/api/v1/projects/{project.id}/channels/{channel_id}",
        json={"display_name": "new", "is_active": False},
    )
    assert resp.status_code == 200
    assert resp.json()["display_name"] == "new"
    assert resp.json()["is_active"] is False


async def test_delete_channel(client: AsyncClient, db_session):
    project = await _make_project(db_session)
    create = await client.post(
        f"/api/v1/projects/{project.id}/channels",
        json={"kind": "slack", "external_channel_id": "C1"},
    )
    channel_id = create.json()["id"]
    resp = await client.delete(f"/api/v1/projects/{project.id}/channels/{channel_id}")
    assert resp.status_code == 204

    listing = await client.get(f"/api/v1/projects/{project.id}/channels")
    assert listing.json() == []


# ── Slack adapter ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_slack_adapter_posts_to_slack_api():
    sent_payloads: list[dict] = []

    class FakeResponse:
        def json(self) -> dict:
            return {"ok": True}

    async def fake_post(url, headers=None, json=None):
        sent_payloads.append({"url": url, "headers": headers, "json": json})
        return FakeResponse()

    fake_http = MagicMock()
    fake_http.post = AsyncMock(side_effect=fake_post)

    adapter = SlackChannelAdapter(http_client=fake_http)
    target = ChannelTarget(
        kind="slack",
        external_channel_id="C99",
        credentials={"bot_token": "xoxb-test"},
    )

    await adapter.post_message(target, "Build complete", role="assistant", agent_name="CTO")

    assert len(sent_payloads) == 1
    payload = sent_payloads[0]
    assert payload["url"] == "https://slack.com/api/chat.postMessage"
    assert payload["headers"]["Authorization"] == "Bearer xoxb-test"
    assert payload["json"]["channel"] == "C99"
    assert "*CTO*" in payload["json"]["text"]
    assert "Build complete" in payload["json"]["text"]


@pytest.mark.asyncio
async def test_slack_adapter_skips_when_no_token(caplog):
    fake_http = MagicMock()
    fake_http.post = AsyncMock()
    adapter = SlackChannelAdapter(http_client=fake_http)
    target = ChannelTarget(kind="slack", external_channel_id="C99", credentials={})
    await adapter.post_message(target, "msg", role="assistant", agent_name=None)
    fake_http.post.assert_not_called()


@pytest.mark.asyncio
async def test_slack_adapter_handles_api_error_gracefully():
    class FakeResponse:
        def json(self) -> dict:
            return {"ok": False, "error": "channel_not_found"}

    fake_http = MagicMock()
    fake_http.post = AsyncMock(return_value=FakeResponse())

    adapter = SlackChannelAdapter(http_client=fake_http)
    target = ChannelTarget(
        kind="slack",
        external_channel_id="C-bad",
        credentials={"bot_token": "xoxb-test"},
    )
    # Must not raise even though Slack rejects the call.
    await adapter.post_message(target, "msg", role="assistant", agent_name=None)
