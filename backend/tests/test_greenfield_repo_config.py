"""Tests for the greenfield per-project repo-config admin (G8.0).

Reuses the pre-existing ``projects.github_repo_url`` / ``github_branch``
/ ``github_token_encrypted`` columns; no new table.

Wire shape:
  GET    /api/v1/repo-config?project_id=<uuid>  →  redacted or null
  PUT    /api/v1/repo-config?project_id=<uuid>  →  upsert
  DELETE /api/v1/repo-config?project_id=<uuid>  →  clears all three

Secrets at rest: ``github_token_encrypted`` via EncryptionManager.
Wire shape exposes ``has_token`` only.
"""

from __future__ import annotations

import uuid

import pytest

from backend.src.config import settings as app_settings
from backend.src.core.encryption import EncryptionManager
from backend.src.models import Project, Tenant
from backend.src.models.project import WorkspaceType


async def _seed_project(db_session, tenant_id: uuid.UUID, name: str = "Test Project") -> Project:
    project = Project(
        tenant_id=tenant_id,
        name=name,
        description="",
        workspace_type=WorkspaceType.server_managed,
    )
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)
    return project


@pytest.mark.asyncio
async def test_get_repo_config_returns_null_when_not_configured(client, db_session, mock_tenant_id, seeded_tenant):
    project = await _seed_project(db_session, mock_tenant_id)
    resp = await client.get(
        "/api/v1/repo-config",
        params={"project_id": str(project.id)},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() is None


@pytest.mark.asyncio
async def test_get_repo_config_404_when_project_missing(client, mock_tenant_id, seeded_tenant):
    resp = await client.get(
        "/api/v1/repo-config",
        params={"project_id": str(uuid.uuid4())},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_put_repo_config_creates_binding_and_encrypts_token(client, db_session, mock_tenant_id, seeded_tenant):
    project = await _seed_project(db_session, mock_tenant_id)
    resp = await client.put(
        "/api/v1/repo-config",
        params={"project_id": str(project.id)},
        json={
            "repo_url": "https://github.com/acme/widget",
            "branch": "main",
            "token": "ghp_pretend_personal_access_token",
        },
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {
        "project_id": str(project.id),
        "repo_url": "https://github.com/acme/widget",
        "branch": "main",
        "has_token": True,
    }

    await db_session.refresh(project)
    assert project.github_repo_url == "https://github.com/acme/widget"
    assert project.github_branch == "main"
    assert project.github_token_encrypted, "token should be encrypted on disk"
    decrypted = EncryptionManager(app_settings.encryption_key).decrypt_value(project.github_token_encrypted)
    assert decrypted == "ghp_pretend_personal_access_token"


@pytest.mark.asyncio
async def test_put_repo_config_works_without_token(client, db_session, mock_tenant_id, seeded_tenant):
    project = await _seed_project(db_session, mock_tenant_id)
    resp = await client.put(
        "/api/v1/repo-config",
        params={"project_id": str(project.id)},
        json={
            "repo_url": "https://github.com/acme/public",
            "branch": "main",
        },
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["has_token"] is False
    await db_session.refresh(project)
    assert project.github_token_encrypted is None


@pytest.mark.asyncio
async def test_put_preserves_token_when_field_omitted(client, db_session, mock_tenant_id, seeded_tenant):
    project = await _seed_project(db_session, mock_tenant_id)
    # First call seeds a token.
    await client.put(
        "/api/v1/repo-config",
        params={"project_id": str(project.id)},
        json={"repo_url": "https://github.com/a/b", "branch": "main", "token": "tok1"},
        headers={"Authorization": "Bearer fake"},
    )
    # Second call updates url + branch but omits token.
    resp = await client.put(
        "/api/v1/repo-config",
        params={"project_id": str(project.id)},
        json={"repo_url": "https://github.com/a/b", "branch": "release/v2"},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["has_token"] is True
    await db_session.refresh(project)
    assert project.github_branch == "release/v2"
    decrypted = EncryptionManager(app_settings.encryption_key).decrypt_value(project.github_token_encrypted)
    assert decrypted == "tok1"


@pytest.mark.asyncio
async def test_put_clears_token_when_field_is_null(client, db_session, mock_tenant_id, seeded_tenant):
    project = await _seed_project(db_session, mock_tenant_id)
    await client.put(
        "/api/v1/repo-config",
        params={"project_id": str(project.id)},
        json={"repo_url": "https://github.com/a/b", "branch": "main", "token": "tok"},
        headers={"Authorization": "Bearer fake"},
    )
    resp = await client.put(
        "/api/v1/repo-config",
        params={"project_id": str(project.id)},
        json={"repo_url": "https://github.com/a/b", "branch": "main", "token": None},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["has_token"] is False
    await db_session.refresh(project)
    assert project.github_token_encrypted is None


@pytest.mark.asyncio
async def test_put_rejects_non_https_repo_url(client, db_session, mock_tenant_id, seeded_tenant):
    project = await _seed_project(db_session, mock_tenant_id)
    resp = await client.put(
        "/api/v1/repo-config",
        params={"project_id": str(project.id)},
        json={"repo_url": "git@github.com:a/b.git", "branch": "main"},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_put_rejects_extra_fields(client, db_session, mock_tenant_id, seeded_tenant):
    project = await _seed_project(db_session, mock_tenant_id)
    resp = await client.put(
        "/api/v1/repo-config",
        params={"project_id": str(project.id)},
        json={
            "repo_url": "https://github.com/a/b",
            "branch": "main",
            "extra_field": "rejected",
        },
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_delete_clears_repo_binding(client, db_session, mock_tenant_id, seeded_tenant):
    project = await _seed_project(db_session, mock_tenant_id)
    await client.put(
        "/api/v1/repo-config",
        params={"project_id": str(project.id)},
        json={"repo_url": "https://github.com/a/b", "branch": "main", "token": "tok"},
        headers={"Authorization": "Bearer fake"},
    )
    resp = await client.delete(
        "/api/v1/repo-config",
        params={"project_id": str(project.id)},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 204, resp.text
    await db_session.refresh(project)
    assert project.github_repo_url is None
    assert project.github_branch is None
    assert project.github_token_encrypted is None

    # GET after delete returns null again.
    resp = await client.get(
        "/api/v1/repo-config",
        params={"project_id": str(project.id)},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200
    assert resp.json() is None


@pytest.mark.asyncio
async def test_repo_config_is_tenant_scoped(client, db_session, mock_tenant_id, seeded_tenant):
    # Seed a project belonging to a DIFFERENT tenant.
    other_tenant_id = uuid.uuid4()
    other_tenant = Tenant(
        id=other_tenant_id,
        name="Other Tenant",
        slug=f"other-{other_tenant_id.hex[:8]}",
        owner_user_id="other-user-id",
    )
    db_session.add(other_tenant)
    await db_session.commit()
    other_project = Project(
        tenant_id=other_tenant_id,
        name="Other Project",
        description="",
        workspace_type=WorkspaceType.server_managed,
        github_repo_url="https://github.com/other/leak",
        github_branch="main",
    )
    db_session.add(other_project)
    await db_session.commit()

    # Caller is mock_tenant_id; lookup of other tenant's project must 404.
    resp = await client.get(
        "/api/v1/repo-config",
        params={"project_id": str(other_project.id)},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 404
    resp = await client.put(
        "/api/v1/repo-config",
        params={"project_id": str(other_project.id)},
        json={"repo_url": "https://github.com/hijack/me", "branch": "main"},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_put_404_when_project_missing(client, mock_tenant_id, seeded_tenant):
    resp = await client.put(
        "/api/v1/repo-config",
        params={"project_id": str(uuid.uuid4())},
        json={"repo_url": "https://github.com/a/b", "branch": "main"},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_404_when_project_missing(client, mock_tenant_id, seeded_tenant):
    resp = await client.delete(
        "/api/v1/repo-config",
        params={"project_id": str(uuid.uuid4())},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_put_repo_config_flips_workspace_type_to_github_connected(
    client, db_session, mock_tenant_id, seeded_tenant
):
    """Phase 3: binding a repo IS connecting the project to it — the
    orchestrator clones a github_connected project's repo into the
    workspace. A founder 'connects a repo'; the workspace_type knob
    stays invisible."""
    project = await _seed_project(db_session, mock_tenant_id)
    assert project.workspace_type == WorkspaceType.server_managed
    await client.put(
        "/api/v1/repo-config",
        params={"project_id": str(project.id)},
        json={"repo_url": "https://github.com/a/b", "branch": "main", "token": "tok"},
        headers={"Authorization": "Bearer fake"},
    )
    await db_session.refresh(project)
    assert project.workspace_type == WorkspaceType.github_connected


@pytest.mark.asyncio
async def test_delete_repo_config_reverts_workspace_type_to_server_managed(
    client, db_session, mock_tenant_id, seeded_tenant
):
    """Clearing the repo binding hands the project back to a managed
    workspace — there is no longer a repo to clone."""
    project = await _seed_project(db_session, mock_tenant_id)
    await client.put(
        "/api/v1/repo-config",
        params={"project_id": str(project.id)},
        json={"repo_url": "https://github.com/a/b", "branch": "main", "token": "tok"},
        headers={"Authorization": "Bearer fake"},
    )
    await db_session.refresh(project)
    assert project.workspace_type == WorkspaceType.github_connected

    resp = await client.delete(
        "/api/v1/repo-config",
        params={"project_id": str(project.id)},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 204, resp.text
    await db_session.refresh(project)
    assert project.workspace_type == WorkspaceType.server_managed


@pytest.mark.asyncio
async def test_delete_repo_config_leaves_non_github_workspace_type(
    client, db_session, mock_tenant_id, seeded_tenant
):
    """A DELETE on a project that was never github_connected (e.g. a
    local_import project) must not silently flip it."""
    project = await _seed_project(db_session, mock_tenant_id)
    project.workspace_type = WorkspaceType.local_import
    await db_session.commit()
    resp = await client.delete(
        "/api/v1/repo-config",
        params={"project_id": str(project.id)},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 204, resp.text
    await db_session.refresh(project)
    assert project.workspace_type == WorkspaceType.local_import
