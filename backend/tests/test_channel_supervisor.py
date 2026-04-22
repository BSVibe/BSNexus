"""Tests for the ChannelSupervisor reconciliation tick."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from backend.src.core.channel_supervisor import ChannelSupervisor
from backend.src.models import Project, ProjectChannel, ProjectStatus


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
    await db_session.flush()
    return project


def _wrap_session(session):
    """Mimic ``async with async_session() as db:`` for tests."""

    class _Wrap:
        def __init__(self, sess):
            self._sess = sess

        async def __aenter__(self):
            return self._sess

        async def __aexit__(self, exc_type, exc, tb):
            return False

    return lambda: _Wrap(session)


@pytest.fixture
def stream_mock() -> AsyncMock:
    m = AsyncMock()
    m.publish = AsyncMock(return_value="msg-1")
    # ChannelFanout.run() spins on tail(); return an empty list immediately
    # so tests don't hang on the 15s default block.
    m.tail = AsyncMock(return_value=[])
    return m


async def test_tick_starts_fanout_for_active_channels(db_session, stream_mock, monkeypatch):
    project = await _make_project(db_session)
    db_session.add(
        ProjectChannel(
            project_id=project.id,
            kind="slack",
            external_channel_id="C1",
            credentials_encrypted=json.dumps({"bot_token": "xoxb-test"}),
            is_active=True,
        )
    )
    await db_session.commit()

    monkeypatch.setattr(
        "backend.src.core.channel_supervisor.async_session", _wrap_session(db_session)
    )

    sup = ChannelSupervisor(stream_mock)
    try:
        await sup.tick()
        assert project.id in sup._fanouts
    finally:
        await sup.stop()


async def test_tick_stops_fanout_when_channels_disabled(db_session, stream_mock, monkeypatch):
    project = await _make_project(db_session)
    channel = ProjectChannel(
        project_id=project.id,
        kind="slack",
        external_channel_id="C1",
        credentials_encrypted=json.dumps({"bot_token": "xoxb-test"}),
        is_active=True,
    )
    db_session.add(channel)
    await db_session.commit()

    monkeypatch.setattr(
        "backend.src.core.channel_supervisor.async_session", _wrap_session(db_session)
    )

    sup = ChannelSupervisor(stream_mock)
    try:
        await sup.tick()
        assert project.id in sup._fanouts

        channel.is_active = False
        await db_session.commit()

        await sup.tick()
        assert project.id not in sup._fanouts
    finally:
        await sup.stop()


async def test_tick_skips_projects_with_no_active_channels(db_session, stream_mock, monkeypatch):
    project = await _make_project(db_session)
    await db_session.commit()

    monkeypatch.setattr(
        "backend.src.core.channel_supervisor.async_session", _wrap_session(db_session)
    )

    sup = ChannelSupervisor(stream_mock)
    try:
        await sup.tick()
        assert sup._fanouts == {}
    finally:
        await sup.stop()


async def test_start_stop_idempotent(stream_mock):
    sup = ChannelSupervisor(stream_mock)
    sup.start()
    sup.start()
    await sup.stop()
    await sup.stop()
