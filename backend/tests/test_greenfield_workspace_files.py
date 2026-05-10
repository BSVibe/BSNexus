"""Tests for the greenfield workspace-files admin (G7.5c).

Founder needs file/folder visibility for design + code reviews. Without
a workspace surface, deliverables that reference paths in the project
workspace (artifact_refs) have nowhere to land — the founder can read
the *summary* of a deliverable but not the actual file the verifier
signed off on.

Endpoints (per CLAUDE.md flat REST shape, A3):
  GET /api/v1/workspace-files?project_id=X        — directory listing
  GET /api/v1/workspace-files/content?project_id=X&path=Y — file content

Both are tenant-scoped via the project lookup. Path traversal is
blocked: ``..`` segments and absolute paths must 422.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from backend.src.models.project import Project, WorkspaceType


async def _seed_project(
    db_session,
    mock_tenant_id: uuid.UUID,
    workspace_dir: Path,
) -> Project:
    project = Project(
        tenant_id=mock_tenant_id,
        name="Workspace Test",
        description="",
        workspace_type=WorkspaceType.local_import,
        workspace_dir=str(workspace_dir),
    )
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)
    return project


@pytest.mark.asyncio
async def test_workspace_tree_lists_root_entries(client, db_session, tmp_path, mock_tenant_id, seeded_tenant):
    (tmp_path / "src").mkdir()
    (tmp_path / "README.md").write_text("# hello\n")
    (tmp_path / "src" / "main.py").write_text("print('hi')\n")

    project = await _seed_project(db_session, mock_tenant_id, tmp_path)

    resp = await client.get(
        f"/api/v1/workspace-files?project_id={project.id}",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["path"] == ""
    # Sorted alphabetically, directories first then files (or unified —
    # the contract is just "stable ordering"; we assert by name set).
    names = [entry["name"] for entry in body["entries"]]
    assert names == sorted(names)
    assert "src" in names
    assert "README.md" in names
    src_entry = next(e for e in body["entries"] if e["name"] == "src")
    readme_entry = next(e for e in body["entries"] if e["name"] == "README.md")
    assert src_entry["kind"] == "dir"
    assert readme_entry["kind"] == "file"
    assert readme_entry["size"] == len("# hello\n")


@pytest.mark.asyncio
async def test_workspace_tree_lists_subdir(client, db_session, tmp_path, mock_tenant_id, seeded_tenant):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')\n")
    project = await _seed_project(db_session, mock_tenant_id, tmp_path)

    resp = await client.get(
        f"/api/v1/workspace-files?project_id={project.id}&path=src",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["path"] == "src"
    names = [e["name"] for e in body["entries"]]
    assert names == ["main.py"]


@pytest.mark.asyncio
async def test_workspace_tree_rejects_path_traversal(client, db_session, tmp_path, mock_tenant_id, seeded_tenant):
    project = await _seed_project(db_session, mock_tenant_id, tmp_path)
    # Sibling dir outside the workspace — must not leak.
    sibling = tmp_path.parent / "sibling"
    sibling.mkdir(exist_ok=True)
    (sibling / "secret.txt").write_text("nope")

    resp = await client.get(
        f"/api/v1/workspace-files?project_id={project.id}&path=../sibling",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_workspace_tree_returns_empty_when_no_workspace_dir(client, db_session, mock_tenant_id, seeded_tenant):
    """Server-managed project before any code lands has no workspace
    directory yet; surface returns an empty listing rather than 500 so
    the UI can render the empty state."""
    project = Project(
        tenant_id=mock_tenant_id,
        name="No Workspace",
        workspace_type=WorkspaceType.server_managed,
        workspace_dir=None,
    )
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)

    resp = await client.get(
        f"/api/v1/workspace-files?project_id={project.id}",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"path": "", "entries": []}


@pytest.mark.asyncio
async def test_workspace_tree_404s_for_cross_tenant_project(
    client, db_session, tmp_path, mock_tenant_id, seeded_tenant
):
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
    await db_session.flush()
    other_project = Project(
        tenant_id=other_tenant_id,
        name="Other workspace",
        workspace_type=WorkspaceType.local_import,
        workspace_dir=str(tmp_path),
    )
    db_session.add(other_project)
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/workspace-files?project_id={other_project.id}",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_workspace_content_returns_file_text(client, db_session, tmp_path, mock_tenant_id, seeded_tenant):
    (tmp_path / "README.md").write_text("# hello\n")
    project = await _seed_project(db_session, mock_tenant_id, tmp_path)

    resp = await client.get(
        f"/api/v1/workspace-files/content?project_id={project.id}&path=README.md",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["path"] == "README.md"
    assert body["content"] == "# hello\n"
    assert body["size"] == len("# hello\n")


@pytest.mark.asyncio
async def test_workspace_content_rejects_binary(client, db_session, tmp_path, mock_tenant_id, seeded_tenant):
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x01")
    project = await _seed_project(db_session, mock_tenant_id, tmp_path)

    resp = await client.get(
        f"/api/v1/workspace-files/content?project_id={project.id}&path=logo.png",
        headers={"Authorization": "Bearer fake"},
    )
    # 415 = Unsupported Media Type; the founder UI shows "binary file" and
    # offers no inline view.
    assert resp.status_code == 415, resp.text


@pytest.mark.asyncio
async def test_workspace_content_rejects_oversized_file(client, db_session, tmp_path, mock_tenant_id, seeded_tenant):
    big = "a" * (300 * 1024)  # 300 KiB > 256 KiB cap
    (tmp_path / "big.txt").write_text(big)
    project = await _seed_project(db_session, mock_tenant_id, tmp_path)

    resp = await client.get(
        f"/api/v1/workspace-files/content?project_id={project.id}&path=big.txt",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 413, resp.text


@pytest.mark.asyncio
async def test_workspace_content_rejects_traversal(client, db_session, tmp_path, mock_tenant_id, seeded_tenant):
    project = await _seed_project(db_session, mock_tenant_id, tmp_path)

    resp = await client.get(
        f"/api/v1/workspace-files/content?project_id={project.id}&path=../etc/hosts",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_workspace_content_404_for_missing_file(client, db_session, tmp_path, mock_tenant_id, seeded_tenant):
    project = await _seed_project(db_session, mock_tenant_id, tmp_path)

    resp = await client.get(
        f"/api/v1/workspace-files/content?project_id={project.id}&path=nope.md",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 404
