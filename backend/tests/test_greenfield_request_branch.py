"""Tests for ``ensure_request_branch`` + ``POST /api/v1/requests/{id}/branch``
(G8.1).

Uses ``httpx.MockTransport`` to inject a deterministic GitHub at the
``GithubClient`` layer. The real network is never touched.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import httpx
import pytest

from backend.src.config import settings as app_settings
from backend.src.core.encryption import EncryptionManager
from backend.src.core.git_ops import (
    BranchOpError,
    build_request_branch_name,
    ensure_request_branch,
)
from backend.src.core.github import GithubClient
from backend.src.models import Project, Request, Tenant
from backend.src.models.project import WorkspaceType


def _enc(value: str) -> str:
    return EncryptionManager(app_settings.encryption_key).encrypt_value(value)


async def _seed_project_and_request(
    db_session,
    tenant_id: uuid.UUID,
    *,
    repo_url: str | None = "https://github.com/acme/widget",
    branch: str | None = "main",
    token: str | None = "ghp_secret",
) -> tuple[Project, Request]:
    project = Project(
        tenant_id=tenant_id,
        name="Test Project",
        description="",
        workspace_type=WorkspaceType.server_managed,
        github_repo_url=repo_url,
        github_branch=branch,
        github_token_encrypted=_enc(token) if token else None,
    )
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)
    request_row = Request(
        tenant_id=tenant_id,
        project_id=project.id,
        intent="ship a thing",
    )
    db_session.add(request_row)
    await db_session.commit()
    await db_session.refresh(request_row)
    return project, request_row


def _factory_for(handler: Callable[[httpx.Request], httpx.Response]):
    def _factory(token: str) -> GithubClient:  # noqa: ARG001 — token unused in tests
        transport = httpx.MockTransport(handler)
        http = httpx.AsyncClient(base_url="https://api.github.com", transport=transport)
        return GithubClient(token=token, client=http)

    return _factory


@pytest.mark.asyncio
async def test_ensure_creates_branch_off_base(db_session, mock_tenant_id, seeded_tenant) -> None:
    _, request_row = await _seed_project_and_request(db_session, mock_tenant_id)
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "GET" and request.url.path.endswith("/heads/main"):
            return httpx.Response(200, json={"ref": "refs/heads/main", "object": {"sha": "base-sha", "type": "commit"}})
        if request.method == "GET":
            return httpx.Response(404, json={"message": "Not Found"})
        if request.method == "POST":
            return httpx.Response(
                201,
                json={
                    "ref": f"refs/heads/{build_request_branch_name(request_row.id)}",
                    "object": {"sha": "new-sha", "type": "commit"},
                },
            )
        return httpx.Response(500)  # pragma: no cover

    info = await ensure_request_branch(request=request_row, session=db_session, client_factory=_factory_for(handler))
    assert info.created is True
    assert info.name == build_request_branch_name(request_row.id)
    assert info.sha == "new-sha"
    assert info.base_branch == "main"
    # The first GET that fired was the existence check for the new branch.
    assert calls[0][0] == "GET"
    assert calls[0][1].endswith(f"/heads/{info.name}")


@pytest.mark.asyncio
async def test_ensure_returns_existing_branch_without_create(db_session, mock_tenant_id, seeded_tenant) -> None:
    _, request_row = await _seed_project_and_request(db_session, mock_tenant_id)
    expected_name = build_request_branch_name(request_row.id)
    post_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            post_calls.append(request.url.path)
            return httpx.Response(500)  # pragma: no cover — should not fire
        # All GETs return a populated ref so the existence check short-circuits.
        return httpx.Response(
            200,
            json={
                "ref": f"refs/heads/{expected_name}",
                "object": {"sha": "kept-sha", "type": "commit"},
            },
        )

    info = await ensure_request_branch(request=request_row, session=db_session, client_factory=_factory_for(handler))
    assert info.created is False
    assert info.sha == "kept-sha"
    assert post_calls == []


@pytest.mark.asyncio
async def test_ensure_raises_repo_not_bound(db_session, mock_tenant_id, seeded_tenant) -> None:
    _, request_row = await _seed_project_and_request(db_session, mock_tenant_id, repo_url=None, token=None)
    with pytest.raises(BranchOpError) as exc_info:
        await ensure_request_branch(request=request_row, session=db_session)
    assert exc_info.value.reason == "repo_not_bound"


@pytest.mark.asyncio
async def test_ensure_raises_missing_token(db_session, mock_tenant_id, seeded_tenant) -> None:
    _, request_row = await _seed_project_and_request(db_session, mock_tenant_id, token=None)
    with pytest.raises(BranchOpError) as exc_info:
        await ensure_request_branch(request=request_row, session=db_session)
    assert exc_info.value.reason == "missing_token"


@pytest.mark.asyncio
async def test_ensure_raises_invalid_repo_url(db_session, mock_tenant_id, seeded_tenant) -> None:
    _, request_row = await _seed_project_and_request(
        db_session, mock_tenant_id, repo_url="https://example.com/acme/widget"
    )
    with pytest.raises(BranchOpError) as exc_info:
        await ensure_request_branch(request=request_row, session=db_session)
    assert exc_info.value.reason == "invalid_repo_url"


@pytest.mark.asyncio
async def test_ensure_raises_base_branch_not_found(db_session, mock_tenant_id, seeded_tenant) -> None:
    _, request_row = await _seed_project_and_request(db_session, mock_tenant_id, branch="missing-base")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    with pytest.raises(BranchOpError) as exc_info:
        await ensure_request_branch(request=request_row, session=db_session, client_factory=_factory_for(handler))
    assert exc_info.value.reason == "base_branch_not_found"


@pytest.mark.asyncio
async def test_ensure_raises_github_auth_on_401(db_session, mock_tenant_id, seeded_tenant) -> None:
    _, request_row = await _seed_project_and_request(db_session, mock_tenant_id)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Bad credentials"})

    with pytest.raises(BranchOpError) as exc_info:
        await ensure_request_branch(request=request_row, session=db_session, client_factory=_factory_for(handler))
    assert exc_info.value.reason == "github_auth"


# ────────────────────────────── Endpoint tests ──────────────────────────────


@pytest.mark.asyncio
async def test_post_branch_endpoint_returns_201_with_created(
    client, db_session, mock_tenant_id, seeded_tenant, monkeypatch
) -> None:
    _, request_row = await _seed_project_and_request(db_session, mock_tenant_id)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/heads/main"):
            return httpx.Response(200, json={"ref": "refs/heads/main", "object": {"sha": "base", "type": "commit"}})
        if request.method == "GET":
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(
            201,
            json={
                "ref": f"refs/heads/{build_request_branch_name(request_row.id)}",
                "object": {"sha": "new", "type": "commit"},
            },
        )

    factory = _factory_for(handler)
    monkeypatch.setattr(
        "backend.src.api.repo_branch.ensure_request_branch",
        lambda *, request, session: ensure_request_branch(request=request, session=session, client_factory=factory),
    )

    resp = await client.post(
        f"/api/v1/requests/{request_row.id}/branch",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] is True
    assert body["branch_name"] == build_request_branch_name(request_row.id)
    assert body["sha"] == "new"
    assert body["base_branch"] == "main"


@pytest.mark.asyncio
async def test_post_branch_endpoint_412_when_repo_not_bound(client, db_session, mock_tenant_id, seeded_tenant) -> None:
    _, request_row = await _seed_project_and_request(db_session, mock_tenant_id, repo_url=None, token=None)
    resp = await client.post(
        f"/api/v1/requests/{request_row.id}/branch",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 412, resp.text
    assert resp.json()["detail"]["reason"] == "repo_not_bound"


@pytest.mark.asyncio
async def test_post_branch_endpoint_412_when_token_missing(client, db_session, mock_tenant_id, seeded_tenant) -> None:
    _, request_row = await _seed_project_and_request(db_session, mock_tenant_id, token=None)
    resp = await client.post(
        f"/api/v1/requests/{request_row.id}/branch",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 412
    assert resp.json()["detail"]["reason"] == "missing_token"


@pytest.mark.asyncio
async def test_post_branch_endpoint_502_on_github_auth_error(
    client, db_session, mock_tenant_id, seeded_tenant, monkeypatch
) -> None:
    _, request_row = await _seed_project_and_request(db_session, mock_tenant_id)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Bad credentials"})

    factory = _factory_for(handler)
    monkeypatch.setattr(
        "backend.src.api.repo_branch.ensure_request_branch",
        lambda *, request, session: ensure_request_branch(request=request, session=session, client_factory=factory),
    )

    resp = await client.post(
        f"/api/v1/requests/{request_row.id}/branch",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 502
    assert resp.json()["detail"]["reason"] == "github_auth"


@pytest.mark.asyncio
async def test_post_branch_endpoint_404_when_request_missing(client, mock_tenant_id, seeded_tenant) -> None:
    resp = await client.post(
        f"/api/v1/requests/{uuid.uuid4()}/branch",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_post_branch_endpoint_is_tenant_scoped(client, db_session, mock_tenant_id, seeded_tenant) -> None:
    other_tenant_id = uuid.uuid4()
    other_tenant = Tenant(
        id=other_tenant_id,
        name="Other Tenant",
        slug=f"other-{other_tenant_id.hex[:8]}",
        owner_user_id="other-user-id",
    )
    db_session.add(other_tenant)
    await db_session.commit()
    project = Project(
        tenant_id=other_tenant_id,
        name="Other",
        description="",
        workspace_type=WorkspaceType.server_managed,
        github_repo_url="https://github.com/other/leak",
        github_branch="main",
        github_token_encrypted=_enc("ghp_other"),
    )
    db_session.add(project)
    await db_session.commit()
    other_request = Request(tenant_id=other_tenant_id, project_id=project.id, intent="x")
    db_session.add(other_request)
    await db_session.commit()

    resp = await client.post(
        f"/api/v1/requests/{other_request.id}/branch",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 404
