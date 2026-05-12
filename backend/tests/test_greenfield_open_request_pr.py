"""Tests for ``open_request_pr`` + endpoint + transition_request hook (G8.3).

Uses ``httpx.MockTransport`` to inject a deterministic GitHub at the
``GithubClient`` layer. The real network is never touched.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import httpx
import pytest
from sqlalchemy import select

from backend.src.config import settings as app_settings
from backend.src.core.domain import (
    DeliverableType,
    ProofState,
    RequestStatus,
    WorkPlanCreatedBy,
    WorkStepStatus,
)
from backend.src.core.encryption import EncryptionManager
from backend.src.core.git_ops import (
    PullRequestOpError,
    open_request_pr,
)
from backend.src.core.github import GithubClient
from backend.src.core.work_steps import (
    WorkStepDraft,
    create_work_plan,
    transition_request,
)
from backend.src.models import Deliverable, Project, Request, Tenant, WorkStep
from backend.src.models.project import WorkspaceType


def _enc(value: str) -> str:
    return EncryptionManager(app_settings.encryption_key).encrypt_value(value)


def _factory_for(handler: Callable[[httpx.Request], httpx.Response]):
    def _factory(token: str) -> GithubClient:  # noqa: ARG001
        transport = httpx.MockTransport(handler)
        http = httpx.AsyncClient(base_url="https://api.github.com", transport=transport)
        return GithubClient(token=token, client=http)

    return _factory


async def _seed_shipped_ready(
    db_session,
    tenant_id: uuid.UUID,
    *,
    repo_url: str | None = "https://github.com/acme/widget",
    branch: str | None = "main",
    token: str | None = "ghp_secret",
) -> tuple[Project, Request, WorkStep, Deliverable]:
    """Seed Project + Request + WorkStep(completed) + verified Deliverable
    so the shipped-transition gate passes.
    """
    project = Project(
        tenant_id=tenant_id,
        name="P",
        description="",
        workspace_type=WorkspaceType.local_import,
        workspace_dir="/tmp/ignored",
        github_repo_url=repo_url,
        github_branch=branch,
        github_token_encrypted=_enc(token) if token else None,
    )
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)

    request_row = Request(tenant_id=tenant_id, project_id=project.id, intent="ship a thing")
    db_session.add(request_row)
    await db_session.commit()
    await db_session.refresh(request_row)

    plan = await create_work_plan(
        request=request_row,
        steps=[WorkStepDraft(name="Land", objective="x", expected_outputs=[])],
        created_by=WorkPlanCreatedBy.system,
        session=db_session,
    )
    step = (await db_session.execute(select(WorkStep).where(WorkStep.plan_id == plan.id))).scalar_one()
    step.status = WorkStepStatus.review_ready

    deliverable = Deliverable(
        tenant_id=tenant_id,
        project_id=project.id,
        request_id=request_row.id,
        work_step_id=step.id,
        type=DeliverableType.code,
        title="Add health route",
        artifact_refs=[{"path": "src/api.py"}],
        proof_state=ProofState.verified,
    )
    db_session.add(deliverable)
    # Move Request into review_ready (legal pre-shipped state).
    request_row.status = RequestStatus.review_ready
    await db_session.commit()
    await db_session.refresh(request_row)
    return project, request_row, step, deliverable


def _branch_aware_handler(
    branch_name: str,
    *,
    existing_prs: list[dict] | None = None,
    on_create: dict | None = None,
):
    """Default handler simulating a populated branch + PR API."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path.endswith(f"/git/refs/heads/{branch_name}"):
            return httpx.Response(
                200,
                json={"ref": f"refs/heads/{branch_name}", "object": {"sha": "branch-sha", "type": "commit"}},
            )
        if request.method == "GET" and path.endswith("/pulls"):
            return httpx.Response(200, json=existing_prs or [])
        if request.method == "POST" and path.endswith("/pulls"):
            return httpx.Response(
                201,
                json=on_create
                or {
                    "number": 7,
                    "html_url": "https://github.com/acme/widget/pull/7",
                    "state": "open",
                },
            )
        return httpx.Response(500, json={"message": "unexpected"})

    return handler


@pytest.mark.asyncio
async def test_open_request_pr_creates_pr_when_branch_has_commits(db_session, mock_tenant_id, seeded_tenant) -> None:
    _, request_row, _, _ = await _seed_shipped_ready(db_session, mock_tenant_id)
    branch_name = f"bsnexus/req-{request_row.id}"
    handler = _branch_aware_handler(branch_name)
    info = await open_request_pr(request=request_row, session=db_session, client_factory=_factory_for(handler))
    assert info.created is True
    assert info.pr_number == 7
    assert info.pr_url == "https://github.com/acme/widget/pull/7"
    assert info.branch_name == branch_name
    assert info.base_branch == "main"
    # In-memory state stamped.
    assert request_row.pr_number == 7
    assert request_row.pr_url == "https://github.com/acme/widget/pull/7"


@pytest.mark.asyncio
async def test_open_request_pr_returns_existing_pr_without_creating(db_session, mock_tenant_id, seeded_tenant) -> None:
    _, request_row, _, _ = await _seed_shipped_ready(db_session, mock_tenant_id)
    branch_name = f"bsnexus/req-{request_row.id}"
    existing = [{"number": 42, "html_url": "https://github.com/acme/widget/pull/42", "state": "open"}]
    create_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/pulls"):
            create_calls.append(request.url.path)
            return httpx.Response(500)  # pragma: no cover — must not fire
        if request.method == "GET" and request.url.path.endswith(f"/git/refs/heads/{branch_name}"):
            return httpx.Response(
                200,
                json={"ref": f"refs/heads/{branch_name}", "object": {"sha": "x", "type": "commit"}},
            )
        if request.method == "GET" and request.url.path.endswith("/pulls"):
            return httpx.Response(200, json=existing)
        return httpx.Response(500)  # pragma: no cover

    info = await open_request_pr(request=request_row, session=db_session, client_factory=_factory_for(handler))
    assert info.created is False
    assert info.pr_number == 42
    assert create_calls == []


@pytest.mark.asyncio
async def test_open_request_pr_raises_repo_not_bound(db_session, mock_tenant_id, seeded_tenant) -> None:
    _, request_row, _, _ = await _seed_shipped_ready(db_session, mock_tenant_id, repo_url=None, token=None)
    project = await db_session.get(Project, request_row.project_id)
    assert project is not None
    project.github_repo_url = None
    project.github_token_encrypted = None
    await db_session.commit()
    with pytest.raises(PullRequestOpError) as exc_info:
        await open_request_pr(request=request_row, session=db_session)
    assert exc_info.value.reason == "repo_not_bound"


@pytest.mark.asyncio
async def test_open_request_pr_raises_missing_token(db_session, mock_tenant_id, seeded_tenant) -> None:
    _, request_row, _, _ = await _seed_shipped_ready(db_session, mock_tenant_id, token=None)
    with pytest.raises(PullRequestOpError) as exc_info:
        await open_request_pr(request=request_row, session=db_session)
    assert exc_info.value.reason == "missing_token"


@pytest.mark.asyncio
async def test_open_request_pr_raises_no_request_branch_when_just_created(
    db_session, mock_tenant_id, seeded_tenant
) -> None:
    _, request_row, _, _ = await _seed_shipped_ready(db_session, mock_tenant_id)
    branch_name = f"bsnexus/req-{request_row.id}"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path.endswith(f"/git/refs/heads/{branch_name}"):
            return httpx.Response(404, json={"message": "Not Found"})
        if request.method == "GET" and "/git/refs/heads/" in path:
            return httpx.Response(200, json={"ref": "refs/heads/main", "object": {"sha": "base", "type": "commit"}})
        if request.method == "POST" and path.endswith("/git/refs"):
            return httpx.Response(
                201,
                json={"ref": f"refs/heads/{branch_name}", "object": {"sha": "fresh", "type": "commit"}},
            )
        return httpx.Response(500)  # pragma: no cover

    with pytest.raises(PullRequestOpError) as exc_info:
        await open_request_pr(request=request_row, session=db_session, client_factory=_factory_for(handler))
    assert exc_info.value.reason == "no_request_branch"


@pytest.mark.asyncio
async def test_open_request_pr_maps_empty_branch_422(db_session, mock_tenant_id, seeded_tenant) -> None:
    _, request_row, _, _ = await _seed_shipped_ready(db_session, mock_tenant_id)
    branch_name = f"bsnexus/req-{request_row.id}"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path.endswith(f"/git/refs/heads/{branch_name}"):
            return httpx.Response(
                200,
                json={"ref": f"refs/heads/{branch_name}", "object": {"sha": "x", "type": "commit"}},
            )
        if request.method == "GET" and path.endswith("/pulls"):
            return httpx.Response(200, json=[])
        if request.method == "POST" and path.endswith("/pulls"):
            return httpx.Response(422, json={"message": "No commits between main and bsnexus/req-x"})
        return httpx.Response(500)  # pragma: no cover

    with pytest.raises(PullRequestOpError) as exc_info:
        await open_request_pr(request=request_row, session=db_session, client_factory=_factory_for(handler))
    assert exc_info.value.reason == "empty_branch"


@pytest.mark.asyncio
async def test_open_request_pr_maps_auth_error(db_session, mock_tenant_id, seeded_tenant) -> None:
    _, request_row, _, _ = await _seed_shipped_ready(db_session, mock_tenant_id)
    branch_name = f"bsnexus/req-{request_row.id}"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path.endswith(f"/git/refs/heads/{branch_name}"):
            return httpx.Response(
                200,
                json={"ref": f"refs/heads/{branch_name}", "object": {"sha": "x", "type": "commit"}},
            )
        return httpx.Response(401, json={"message": "Bad credentials"})

    with pytest.raises(PullRequestOpError) as exc_info:
        await open_request_pr(request=request_row, session=db_session, client_factory=_factory_for(handler))
    assert exc_info.value.reason == "github_auth"


# ────────────────────── transition_request hook ──────────────────────


@pytest.mark.asyncio
async def test_transition_to_shipped_opens_pr_on_bound_project(db_session, mock_tenant_id, seeded_tenant) -> None:
    _, request_row, _, _ = await _seed_shipped_ready(db_session, mock_tenant_id)
    branch_name = f"bsnexus/req-{request_row.id}"
    handler = _branch_aware_handler(branch_name)

    updated = await transition_request(
        request=request_row,
        target=RequestStatus.shipped,
        session=db_session,
        github_client_factory=_factory_for(handler),
    )
    assert updated.status == RequestStatus.shipped
    assert updated.pr_number == 7
    assert updated.pr_url == "https://github.com/acme/widget/pull/7"


@pytest.mark.asyncio
async def test_transition_to_shipped_keeps_shipped_on_pr_failure(db_session, mock_tenant_id, seeded_tenant) -> None:
    """A 5xx during open_request_pr must NOT revert ``shipped``."""
    _, request_row, _, _ = await _seed_shipped_ready(db_session, mock_tenant_id)
    branch_name = f"bsnexus/req-{request_row.id}"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path.endswith(f"/git/refs/heads/{branch_name}"):
            return httpx.Response(
                200,
                json={"ref": f"refs/heads/{branch_name}", "object": {"sha": "x", "type": "commit"}},
            )
        return httpx.Response(503, json={"message": "down"})

    updated = await transition_request(
        request=request_row,
        target=RequestStatus.shipped,
        session=db_session,
        github_client_factory=_factory_for(handler),
    )
    assert updated.status == RequestStatus.shipped
    assert updated.pr_number is None
    assert updated.pr_url is None


@pytest.mark.asyncio
async def test_transition_to_shipped_skips_pr_when_unbound(db_session, mock_tenant_id, seeded_tenant) -> None:
    """No repo binding → no GithubClient factory call, no PR."""
    _, request_row, _, _ = await _seed_shipped_ready(db_session, mock_tenant_id, repo_url=None, token=None)
    factory_calls: list[str] = []

    def factory(token: str) -> GithubClient:
        factory_calls.append(token)
        raise AssertionError("client factory should not be invoked")  # pragma: no cover

    updated = await transition_request(
        request=request_row,
        target=RequestStatus.shipped,
        session=db_session,
        github_client_factory=factory,
    )
    assert updated.status == RequestStatus.shipped
    assert updated.pr_number is None
    assert factory_calls == []


# ────────────────────────────── Endpoint ──────────────────────────────


@pytest.mark.asyncio
async def test_endpoint_returns_pr_info(client, db_session, mock_tenant_id, seeded_tenant, monkeypatch) -> None:
    _, request_row, _, _ = await _seed_shipped_ready(db_session, mock_tenant_id)
    branch_name = f"bsnexus/req-{request_row.id}"
    handler = _branch_aware_handler(branch_name)
    factory = _factory_for(handler)
    monkeypatch.setattr(
        "backend.src.api.repo_pull_request.open_request_pr",
        lambda *, request, session: open_request_pr(request=request, session=session, client_factory=factory),
    )
    resp = await client.post(
        f"/api/v1/requests/{request_row.id}/pr",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pr_number"] == 7
    assert body["pr_url"] == "https://github.com/acme/widget/pull/7"
    assert body["created"] is True


@pytest.mark.asyncio
async def test_endpoint_412_repo_not_bound(client, db_session, mock_tenant_id, seeded_tenant) -> None:
    _, request_row, _, _ = await _seed_shipped_ready(db_session, mock_tenant_id, repo_url=None, token=None)
    resp = await client.post(
        f"/api/v1/requests/{request_row.id}/pr",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 412
    assert resp.json()["detail"]["reason"] == "repo_not_bound"


@pytest.mark.asyncio
async def test_endpoint_404_missing_request(client, mock_tenant_id, seeded_tenant) -> None:
    resp = await client.post(
        f"/api/v1/requests/{uuid.uuid4()}/pr",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_endpoint_is_tenant_scoped(client, db_session, mock_tenant_id, seeded_tenant) -> None:
    other_tenant_id = uuid.uuid4()
    other_tenant = Tenant(
        id=other_tenant_id,
        name="Other",
        slug=f"other-{other_tenant_id.hex[:8]}",
        owner_user_id="other-user-id",
    )
    db_session.add(other_tenant)
    await db_session.commit()
    project = Project(
        tenant_id=other_tenant_id,
        name="Other P",
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
        f"/api/v1/requests/{other_request.id}/pr",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 404
