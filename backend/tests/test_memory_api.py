"""Tests for the long-term memory API and LocalMemoryProvider."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient

from backend.src.core.memory import LocalMemoryProvider
from backend.src.models import Project, ProjectStatus


async def _make_project(db_session) -> Project:
    now = datetime.now(timezone.utc)
    project = Project(
        id=uuid.uuid4(),
        name="Memory Project",
        description="",
        status=ProjectStatus.active,
        created_at=now,
        updated_at=now,
    )
    db_session.add(project)
    await db_session.commit()
    return project


# ── LocalMemoryProvider unit tests ───────────────────────────────────


@pytest.mark.asyncio
async def test_local_provider_remember_and_recall(db_session):
    project = await _make_project(db_session)
    provider = LocalMemoryProvider(db_session)

    record = await provider.remember(
        project.id,
        agent_id=None,
        category="decision",
        title="Use SQLite for tests",
        content="Tests use aiosqlite for the in-memory DB.",
    )
    assert record.id

    records = await provider.recall(project.id)
    assert len(records) == 1
    assert records[0].title == "Use SQLite for tests"


@pytest.mark.asyncio
async def test_local_provider_filters_by_category(db_session):
    project = await _make_project(db_session)
    provider = LocalMemoryProvider(db_session)
    await provider.remember(project.id, None, category="decision", title="A", content="...")
    await provider.remember(project.id, None, category="learning", title="B", content="...")

    decisions = await provider.recall(project.id, category="decision")
    learnings = await provider.recall(project.id, category="learning")
    assert [r.title for r in decisions] == ["A"]
    assert [r.title for r in learnings] == ["B"]


@pytest.mark.asyncio
async def test_local_provider_forget_removes_record(db_session):
    project = await _make_project(db_session)
    provider = LocalMemoryProvider(db_session)
    record = await provider.remember(project.id, None, category="decision", title="X", content="...")

    deleted = await provider.forget(record.id)
    assert deleted is True
    assert await provider.recall(project.id) == []


@pytest.mark.asyncio
async def test_local_provider_forget_returns_false_for_missing(db_session):
    provider = LocalMemoryProvider(db_session)
    deleted = await provider.forget(uuid.uuid4())
    assert deleted is False


# ── REST API ─────────────────────────────────────────────────────────


async def test_create_and_list_memories(client: AsyncClient, db_session):
    project = await _make_project(db_session)
    resp = await client.post(
        f"/api/v1/projects/{project.id}/memories",
        json={
            "category": "decision",
            "title": "Pick Tailwind",
            "content": "We picked Tailwind because the team is comfortable with it.",
            "metadata": {"source": "team chat"},
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "Pick Tailwind"
    assert body["metadata"]["source"] == "team chat"

    listing = await client.get(f"/api/v1/projects/{project.id}/memories")
    assert listing.status_code == 200
    assert len(listing.json()) == 1


async def test_list_memories_filters_by_category(client: AsyncClient, db_session):
    project = await _make_project(db_session)
    await client.post(
        f"/api/v1/projects/{project.id}/memories",
        json={"category": "decision", "title": "A", "content": "x"},
    )
    await client.post(
        f"/api/v1/projects/{project.id}/memories",
        json={"category": "learning", "title": "B", "content": "y"},
    )
    resp = await client.get(
        f"/api/v1/projects/{project.id}/memories", params={"category": "learning"}
    )
    titles = [m["title"] for m in resp.json()]
    assert titles == ["B"]


async def test_delete_memory(client: AsyncClient, db_session):
    project = await _make_project(db_session)
    create = await client.post(
        f"/api/v1/projects/{project.id}/memories",
        json={"category": "decision", "title": "Forget me", "content": "..."},
    )
    memory_id = create.json()["id"]
    resp = await client.delete(f"/api/v1/projects/{project.id}/memories/{memory_id}")
    assert resp.status_code == 204

    listing = await client.get(f"/api/v1/projects/{project.id}/memories")
    assert listing.json() == []


async def test_delete_missing_memory_returns_404(client: AsyncClient, db_session):
    project = await _make_project(db_session)
    resp = await client.delete(f"/api/v1/projects/{project.id}/memories/{uuid.uuid4()}")
    assert resp.status_code == 404
