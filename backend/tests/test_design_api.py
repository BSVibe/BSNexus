"""Tests for the workspace-backed design tool API."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from httpx import AsyncClient

from backend.src.api import design as design_module
from backend.src.core.workspace import LocalStorageBackend, WorkspaceService
from backend.src.models import Project, ProjectStatus


@pytest.fixture
def workspace_root(tmp_path: Path, monkeypatch) -> Path:
    """Point the design API at a fresh tmp workspace root for each test."""
    root = tmp_path / "workspaces"
    root.mkdir(parents=True, exist_ok=True)
    fresh_service = WorkspaceService(LocalStorageBackend(str(root)))
    monkeypatch.setattr(design_module, "_workspace_service", fresh_service)
    return root


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


# ── DesignSystem (system.bsd) ────────────────────────────────────────


async def test_get_design_system_lazy_creates_file(client: AsyncClient, db_session, workspace_root: Path):
    project = await _make_project(db_session)
    resp = await client.get(f"/api/v1/projects/{project.id}/design/system")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Default"
    assert body["tokens"] == {}
    assert body["path"] == "design/system.bsd"

    # Confirm the file actually landed in the workspace.
    on_disk = workspace_root / str(project.id) / "design" / "system.bsd"
    assert on_disk.exists()
    data = json.loads(on_disk.read_text())
    assert data["name"] == "Default"


async def test_get_design_system_404_for_missing_project(client: AsyncClient, workspace_root: Path):
    resp = await client.get(f"/api/v1/projects/{uuid.uuid4()}/design/system")
    assert resp.status_code == 404


async def test_upsert_design_system_persists_tokens(client: AsyncClient, db_session, workspace_root: Path):
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
    assert resp.json()["tokens"]["color"]["primary"] == "#0ea5e9"

    # Reload and confirm persistence.
    reload = await client.get(f"/api/v1/projects/{project.id}/design/system")
    assert reload.json()["tokens"]["spacing"]["sm"] == 4
    assert reload.json()["brand_voice"] == "playful but technical"


# ── Screens (.bsd files) ─────────────────────────────────────────────


async def test_create_screen_writes_bsd_file(client: AsyncClient, db_session, workspace_root: Path):
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
    body = resp.json()
    assert body["slug"] == "login"
    assert body["path"] == "design/screens/login.bsd"
    assert body["spec"]["root"]["type"] == "Form"

    on_disk = workspace_root / str(project.id) / "design" / "screens" / "login.bsd"
    assert on_disk.exists()


async def test_create_screen_avoids_slug_collisions(client: AsyncClient, db_session, workspace_root: Path):
    project = await _make_project(db_session)
    a = await client.post(f"/api/v1/projects/{project.id}/design/screens", json={"name": "Login"})
    b = await client.post(f"/api/v1/projects/{project.id}/design/screens", json={"name": "Login"})
    assert a.json()["slug"] == "login"
    assert b.json()["slug"] == "login-2"


async def test_list_screens_returns_only_bsd_files(client: AsyncClient, db_session, workspace_root: Path):
    project = await _make_project(db_session)
    await client.post(f"/api/v1/projects/{project.id}/design/screens", json={"name": "Home"})
    await client.post(f"/api/v1/projects/{project.id}/design/screens", json={"name": "About"})

    # Drop a non-bsd file in the same dir to confirm it's filtered out.
    stray = workspace_root / str(project.id) / "design" / "screens" / "notes.txt"
    stray.write_text("ignore me")

    resp = await client.get(f"/api/v1/projects/{project.id}/design/screens")
    names = sorted(s["name"] for s in resp.json())
    assert names == ["About", "Home"]


async def test_get_screen_returns_full_payload(client: AsyncClient, db_session, workspace_root: Path):
    project = await _make_project(db_session)
    create = await client.post(
        f"/api/v1/projects/{project.id}/design/screens",
        json={"name": "Login", "spec": {"root": {"type": "Form"}}, "generated_code": "<Login/>"},
    )
    slug = create.json()["slug"]

    resp = await client.get(f"/api/v1/projects/{project.id}/design/screens/{slug}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Login"
    assert body["spec"]["root"]["type"] == "Form"
    assert body["generated_code"] == "<Login/>"


async def test_update_screen_overwrites_file(client: AsyncClient, db_session, workspace_root: Path):
    project = await _make_project(db_session)
    create = await client.post(
        f"/api/v1/projects/{project.id}/design/screens", json={"name": "Login"}
    )
    slug = create.json()["slug"]

    resp = await client.put(
        f"/api/v1/projects/{project.id}/design/screens/{slug}",
        json={"name": "Login", "intent": "Sign in", "spec": {"root": {"type": "Card"}}},
    )
    assert resp.status_code == 200
    assert resp.json()["intent"] == "Sign in"
    assert resp.json()["spec"]["root"]["type"] == "Card"


async def test_update_screen_404_for_missing(client: AsyncClient, db_session, workspace_root: Path):
    project = await _make_project(db_session)
    resp = await client.put(
        f"/api/v1/projects/{project.id}/design/screens/missing",
        json={"name": "Missing"},
    )
    assert resp.status_code == 404


async def test_delete_screen_removes_file(client: AsyncClient, db_session, workspace_root: Path):
    project = await _make_project(db_session)
    create = await client.post(
        f"/api/v1/projects/{project.id}/design/screens", json={"name": "Throwaway"}
    )
    slug = create.json()["slug"]

    resp = await client.delete(f"/api/v1/projects/{project.id}/design/screens/{slug}")
    assert resp.status_code == 204

    on_disk = workspace_root / str(project.id) / "design" / "screens" / f"{slug}.bsd"
    assert not on_disk.exists()


async def test_delete_screen_404_for_missing(client: AsyncClient, db_session, workspace_root: Path):
    project = await _make_project(db_session)
    resp = await client.delete(f"/api/v1/projects/{project.id}/design/screens/missing")
    assert resp.status_code == 404
