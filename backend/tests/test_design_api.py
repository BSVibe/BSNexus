"""Tests for the builtin design tool API."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient

from backend.src.models import Project, ProjectStatus


async def _make_project(db_session) -> Project:
    now = datetime.now(timezone.utc)
    project = Project(
        id=uuid.uuid4(),
        name="Design Project",
        description="",
        status=ProjectStatus.active,
        created_at=now,
        updated_at=now,
    )
    db_session.add(project)
    await db_session.commit()
    return project


# ── DesignSystem ─────────────────────────────────────────────────────


async def test_get_design_system_lazy_creates(client: AsyncClient, db_session) -> None:
    project = await _make_project(db_session)
    resp = await client.get(f"/api/v1/projects/{project.id}/design/system")
    assert resp.status_code == 200
    data = resp.json()
    assert data["project_id"] == str(project.id)
    assert data["name"] == "Default"
    assert data["tokens"] == {}


async def test_get_design_system_404_for_missing_project(client: AsyncClient) -> None:
    resp = await client.get(f"/api/v1/projects/{uuid.uuid4()}/design/system")
    assert resp.status_code == 404


async def test_upsert_design_system_persists_tokens(client: AsyncClient, db_session) -> None:
    project = await _make_project(db_session)
    payload = {
        "name": "BSVibe",
        "tokens": {"color": {"primary": "#0ea5e9"}, "spacing": {"sm": 4}},
        "components": {"Button": {"shape": "rounded"}},
        "patterns": {},
        "brand_voice": "playful but technical",
    }
    resp = await client.put(f"/api/v1/projects/{project.id}/design/system", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "BSVibe"
    assert data["tokens"]["color"]["primary"] == "#0ea5e9"
    assert data["brand_voice"] == "playful but technical"

    # Reload to confirm it persisted, not just echoed.
    resp2 = await client.get(f"/api/v1/projects/{project.id}/design/system")
    assert resp2.json()["tokens"]["spacing"]["sm"] == 4


# ── Screens ──────────────────────────────────────────────────────────


async def test_create_screen_auto_attaches_to_design_system(client: AsyncClient, db_session) -> None:
    project = await _make_project(db_session)
    resp = await client.post(
        f"/api/v1/projects/{project.id}/design/screens",
        json={
            "name": "Login",
            "route": "/login",
            "intent": "authenticate the user",
            "spec": {"root": {"type": "Form"}},
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Login"
    assert data["route"] == "/login"
    assert data["spec"]["root"]["type"] == "Form"
    assert data["design_system_id"]


async def test_list_screens_returns_only_project_screens(client: AsyncClient, db_session) -> None:
    project_a = await _make_project(db_session)
    project_b = await _make_project(db_session)

    await client.post(
        f"/api/v1/projects/{project_a.id}/design/screens",
        json={"name": "ScreenA"},
    )
    await client.post(
        f"/api/v1/projects/{project_b.id}/design/screens",
        json={"name": "ScreenB"},
    )

    list_a = await client.get(f"/api/v1/projects/{project_a.id}/design/screens")
    list_b = await client.get(f"/api/v1/projects/{project_b.id}/design/screens")
    assert [s["name"] for s in list_a.json()] == ["ScreenA"]
    assert [s["name"] for s in list_b.json()] == ["ScreenB"]


async def test_patch_screen_updates_fields(client: AsyncClient, db_session) -> None:
    project = await _make_project(db_session)
    create = await client.post(
        f"/api/v1/projects/{project.id}/design/screens",
        json={"name": "Old"},
    )
    screen_id = create.json()["id"]

    resp = await client.patch(
        f"/api/v1/projects/{project.id}/design/screens/{screen_id}",
        json={"name": "New", "intent": "Sign up"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "New"
    assert resp.json()["intent"] == "Sign up"


async def test_delete_screen_removes_it(client: AsyncClient, db_session) -> None:
    project = await _make_project(db_session)
    create = await client.post(
        f"/api/v1/projects/{project.id}/design/screens",
        json={"name": "Throwaway"},
    )
    screen_id = create.json()["id"]

    resp = await client.delete(f"/api/v1/projects/{project.id}/design/screens/{screen_id}")
    assert resp.status_code == 204

    resp2 = await client.get(f"/api/v1/projects/{project.id}/design/screens/{screen_id}")
    assert resp2.status_code == 404
