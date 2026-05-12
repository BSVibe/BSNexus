"""Tests for ``compose_pr_body`` + PR-body refresh on open_request_pr (G8.4)."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable

import httpx
import pytest
from sqlalchemy import select

from backend.src.config import settings as app_settings
from backend.src.core.domain import (
    DeliverableType,
    ProofAttemptStatus,
    ProofState,
    WorkPlanCreatedBy,
)
from backend.src.core.encryption import EncryptionManager
from backend.src.core.git_ops import compose_pr_body, open_request_pr
from backend.src.core.github import GithubClient
from backend.src.core.work_steps import WorkStepDraft, create_work_plan
from backend.src.models import (
    Decision,
    Deliverable,
    ProofAttempt,
    Project,
    Request,
    WorkStep,
)
from backend.src.models.project import WorkspaceType


def _enc(value: str) -> str:
    return EncryptionManager(app_settings.encryption_key).encrypt_value(value)


def _factory_for(handler: Callable[[httpx.Request], httpx.Response]):
    def _factory(token: str) -> GithubClient:  # noqa: ARG001
        transport = httpx.MockTransport(handler)
        http = httpx.AsyncClient(base_url="https://api.github.com", transport=transport)
        return GithubClient(token=token, client=http)

    return _factory


async def _seed_project_and_request(
    db_session, tenant_id: uuid.UUID, *, bind_repo: bool = True
) -> tuple[Project, Request, WorkStep]:
    project = Project(
        tenant_id=tenant_id,
        name="P",
        description="",
        workspace_type=WorkspaceType.local_import,
        workspace_dir="/tmp/ignored",
        github_repo_url="https://github.com/acme/widget" if bind_repo else None,
        github_branch="main",
        github_token_encrypted=_enc("ghp_secret") if bind_repo else None,
    )
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)
    request_row = Request(tenant_id=tenant_id, project_id=project.id, intent="Add /healthz route")
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
    return project, request_row, step


@pytest.mark.asyncio
async def test_compose_pr_body_includes_intent_summary(db_session, mock_tenant_id, seeded_tenant) -> None:
    _, request_row, _ = await _seed_project_and_request(db_session, mock_tenant_id)
    body = await compose_pr_body(request=request_row, session=db_session)
    assert "## Summary" in body
    assert "Add /healthz route" in body
    # Footer signature.
    assert "BSNexus" in body
    assert str(request_row.id) in body


@pytest.mark.asyncio
async def test_compose_pr_body_lists_verified_deliverables_with_commit_and_verifier(
    db_session, mock_tenant_id, seeded_tenant
) -> None:
    project, request_row, step = await _seed_project_and_request(db_session, mock_tenant_id)
    deliverable = Deliverable(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        request_id=request_row.id,
        work_step_id=step.id,
        type=DeliverableType.code,
        title="Add /healthz endpoint",
        summary="Returns 200 ok for liveness.",
        artifact_refs=[{"path": "src/api.py"}],
        proof_state=ProofState.verified,
        commit_sha="deadbeefcafefeed1234",
    )
    db_session.add(deliverable)
    await db_session.commit()
    await db_session.refresh(deliverable)
    attempt = ProofAttempt(
        deliverable_id=deliverable.id,
        verifier_type="python_test",
        inputs={"command": ["python", "-m", "pytest"]},
        status=ProofAttemptStatus.verified,
        exit_code=0,
    )
    db_session.add(attempt)
    await db_session.commit()

    body = await compose_pr_body(request=request_row, session=db_session)
    assert "## Verified Deliverables" in body
    assert "Add /healthz endpoint" in body
    assert "`code`" in body
    assert "`deadbeefcafe`" in body  # truncated commit_sha[:12]
    assert "`python -m pytest`" in body
    assert "exit 0" in body
    # Summary inlined.
    assert "Returns 200 ok for liveness." in body


@pytest.mark.asyncio
async def test_compose_pr_body_omits_verified_section_when_none_verified(
    db_session, mock_tenant_id, seeded_tenant
) -> None:
    project, request_row, step = await _seed_project_and_request(db_session, mock_tenant_id)
    # Unverified deliverable — must not show up.
    db_session.add(
        Deliverable(
            tenant_id=mock_tenant_id,
            project_id=project.id,
            request_id=request_row.id,
            work_step_id=step.id,
            type=DeliverableType.code,
            title="Still cooking",
            artifact_refs=[],
            proof_state=ProofState.verification_missing,
        )
    )
    await db_session.commit()
    body = await compose_pr_body(request=request_row, session=db_session)
    assert "Verified Deliverables" not in body


@pytest.mark.asyncio
async def test_compose_pr_body_lists_resolved_decisions(db_session, mock_tenant_id, seeded_tenant) -> None:
    project, request_row, _ = await _seed_project_and_request(db_session, mock_tenant_id)
    db_session.add_all(
        [
            Decision(
                tenant_id=mock_tenant_id,
                project_id=project.id,
                request_id=request_row.id,
                question="Use uv or pip for install instructions?",
                options=["uv", "pip"],
                blocking=False,
                resolution="uv",
                resolved_by="founder@example.com",
                resolved_at=dt.datetime(2026, 5, 12, 12, 0, 0, tzinfo=dt.timezone.utc),
            ),
            # Unresolved — must be omitted.
            Decision(
                tenant_id=mock_tenant_id,
                project_id=project.id,
                request_id=request_row.id,
                question="Wire to OAuth or service account?",
                options=["oauth", "service_account"],
                blocking=False,
            ),
        ]
    )
    await db_session.commit()
    body = await compose_pr_body(request=request_row, session=db_session)
    assert "## Decisions" in body
    assert "Use uv or pip for install instructions?" in body
    assert "uv" in body
    assert "founder@example.com" in body
    # Unresolved decision text not present.
    assert "Wire to OAuth or service account?" not in body


@pytest.mark.asyncio
async def test_compose_pr_body_lists_risks(db_session, mock_tenant_id, seeded_tenant) -> None:
    project, request_row, step = await _seed_project_and_request(db_session, mock_tenant_id)
    db_session.add(
        Deliverable(
            tenant_id=mock_tenant_id,
            project_id=project.id,
            request_id=request_row.id,
            work_step_id=step.id,
            type=DeliverableType.code,
            title="Schema change",
            artifact_refs=[],
            risk_summary="Adds nullable column; backfill required on next deploy.",
            proof_state=ProofState.verified,
        )
    )
    await db_session.commit()
    body = await compose_pr_body(request=request_row, session=db_session)
    assert "## Risks" in body
    assert "Adds nullable column" in body


@pytest.mark.asyncio
async def test_compose_pr_body_handles_empty_request_intent(db_session, mock_tenant_id, seeded_tenant) -> None:
    project, request_row, _ = await _seed_project_and_request(db_session, mock_tenant_id)
    request_row.intent = ""
    await db_session.commit()
    body = await compose_pr_body(request=request_row, session=db_session)
    assert "## Summary" in body
    assert "_(no intent recorded)_" in body


@pytest.mark.asyncio
async def test_compose_pr_body_marks_deliverable_without_commit_sha(db_session, mock_tenant_id, seeded_tenant) -> None:
    project, request_row, step = await _seed_project_and_request(db_session, mock_tenant_id)
    db_session.add(
        Deliverable(
            tenant_id=mock_tenant_id,
            project_id=project.id,
            request_id=request_row.id,
            work_step_id=step.id,
            type=DeliverableType.code,
            title="Added later",
            artifact_refs=[],
            proof_state=ProofState.verified,
            commit_sha=None,
        )
    )
    await db_session.commit()
    body = await compose_pr_body(request=request_row, session=db_session)
    assert "_not yet committed_" in body


# ─────────────────── open_request_pr wiring ───────────────────


def _verified_deliverable_handler(branch_name: str, *, existing: list[dict] | None = None):
    """Handler that satisfies branch + pulls + create_pull. Captures
    PATCH bodies so tests can assert the refresh."""
    captured: dict[str, object] = {"patches": [], "creates": []}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path.endswith(f"/git/refs/heads/{branch_name}"):
            return httpx.Response(
                200,
                json={
                    "ref": f"refs/heads/{branch_name}",
                    "object": {"sha": "x", "type": "commit"},
                },
            )
        if request.method == "GET" and path.endswith("/pulls"):
            return httpx.Response(200, json=existing or [])
        if request.method == "POST" and path.endswith("/pulls"):
            captured["creates"].append(request.content)  # type: ignore[union-attr]
            return httpx.Response(
                201,
                json={
                    "number": 11,
                    "html_url": "https://github.com/acme/widget/pull/11",
                    "state": "open",
                },
            )
        if request.method == "PATCH" and "/pulls/" in path:
            captured["patches"].append(request.content)  # type: ignore[union-attr]
            return httpx.Response(
                200,
                json={
                    "number": 11,
                    "html_url": "https://github.com/acme/widget/pull/11",
                    "state": "open",
                },
            )
        return httpx.Response(500, json={"message": "unexpected"})

    return handler, captured


@pytest.mark.asyncio
async def test_open_request_pr_creates_with_composed_body(db_session, mock_tenant_id, seeded_tenant) -> None:
    project, request_row, step = await _seed_project_and_request(db_session, mock_tenant_id)
    db_session.add(
        Deliverable(
            tenant_id=mock_tenant_id,
            project_id=project.id,
            request_id=request_row.id,
            work_step_id=step.id,
            type=DeliverableType.code,
            title="Add /healthz",
            artifact_refs=[{"path": "src/api.py"}],
            proof_state=ProofState.verified,
            commit_sha="abc123abc123abc123",
        )
    )
    await db_session.commit()
    branch_name = f"bsnexus/req-{request_row.id}"
    handler, captured = _verified_deliverable_handler(branch_name)

    info = await open_request_pr(request=request_row, session=db_session, client_factory=_factory_for(handler))
    assert info.created is True
    # Created PR body must contain the composer output, not the G8.3 placeholder.
    assert len(captured["creates"]) == 1
    create_body = captured["creates"][0]
    assert isinstance(create_body, (bytes, bytearray))
    assert b"## Summary" in create_body
    assert b"Add /healthz route" in create_body
    assert b"## Verified Deliverables" in create_body
    assert b"abc123abc123" in create_body
    assert b"BSNexus PR composer" not in create_body  # placeholder string gone


@pytest.mark.asyncio
async def test_open_request_pr_patches_existing_pr_body(db_session, mock_tenant_id, seeded_tenant) -> None:
    project, request_row, step = await _seed_project_and_request(db_session, mock_tenant_id)
    db_session.add(
        Deliverable(
            tenant_id=mock_tenant_id,
            project_id=project.id,
            request_id=request_row.id,
            work_step_id=step.id,
            type=DeliverableType.code,
            title="Later deliverable",
            artifact_refs=[],
            proof_state=ProofState.verified,
            commit_sha="feedface" * 2,
        )
    )
    await db_session.commit()
    branch_name = f"bsnexus/req-{request_row.id}"
    existing = [{"number": 11, "html_url": "https://github.com/acme/widget/pull/11", "state": "open"}]
    handler, captured = _verified_deliverable_handler(branch_name, existing=existing)

    info = await open_request_pr(request=request_row, session=db_session, client_factory=_factory_for(handler))
    assert info.created is False
    assert info.pr_number == 11
    # PATCH must have fired with the composed body so a re-shipped Request
    # gets a refreshed proof / decisions / risks section.
    assert len(captured["patches"]) == 1
    patch_body = captured["patches"][0]
    assert isinstance(patch_body, (bytes, bytearray))
    assert b"## Summary" in patch_body
    assert b"Later deliverable" in patch_body


@pytest.mark.asyncio
async def test_open_request_pr_existing_continues_on_patch_failure(db_session, mock_tenant_id, seeded_tenant) -> None:
    """A PATCH failure on existing PR must NOT raise — body stays stale,
    but the PR url + number are still surfaced to the caller.
    """
    project, request_row, step = await _seed_project_and_request(db_session, mock_tenant_id)
    db_session.add(
        Deliverable(
            tenant_id=mock_tenant_id,
            project_id=project.id,
            request_id=request_row.id,
            work_step_id=step.id,
            type=DeliverableType.code,
            title="Anything",
            artifact_refs=[],
            proof_state=ProofState.verified,
        )
    )
    await db_session.commit()
    branch_name = f"bsnexus/req-{request_row.id}"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path.endswith(f"/git/refs/heads/{branch_name}"):
            return httpx.Response(
                200,
                json={
                    "ref": f"refs/heads/{branch_name}",
                    "object": {"sha": "x", "type": "commit"},
                },
            )
        if request.method == "GET" and path.endswith("/pulls"):
            return httpx.Response(
                200,
                json=[
                    {
                        "number": 11,
                        "html_url": "https://github.com/acme/widget/pull/11",
                        "state": "open",
                    }
                ],
            )
        if request.method == "PATCH":
            return httpx.Response(503, json={"message": "upstream blip"})
        return httpx.Response(500)  # pragma: no cover

    info = await open_request_pr(request=request_row, session=db_session, client_factory=_factory_for(handler))
    assert info.created is False
    assert info.pr_number == 11


@pytest.mark.asyncio
async def test_open_request_pr_existing_patch_auth_failure_raises(db_session, mock_tenant_id, seeded_tenant) -> None:
    """401/403 on PATCH must surface as github_auth — that signals a
    bad PAT, which we want the founder to see, not silently swallow."""
    project, request_row, step = await _seed_project_and_request(db_session, mock_tenant_id)
    db_session.add(
        Deliverable(
            tenant_id=mock_tenant_id,
            project_id=project.id,
            request_id=request_row.id,
            work_step_id=step.id,
            type=DeliverableType.code,
            title="Anything",
            artifact_refs=[],
            proof_state=ProofState.verified,
        )
    )
    await db_session.commit()
    branch_name = f"bsnexus/req-{request_row.id}"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path.endswith(f"/git/refs/heads/{branch_name}"):
            return httpx.Response(
                200,
                json={
                    "ref": f"refs/heads/{branch_name}",
                    "object": {"sha": "x", "type": "commit"},
                },
            )
        if request.method == "GET" and path.endswith("/pulls"):
            return httpx.Response(
                200,
                json=[
                    {
                        "number": 11,
                        "html_url": "https://github.com/acme/widget/pull/11",
                        "state": "open",
                    }
                ],
            )
        if request.method == "PATCH":
            return httpx.Response(401, json={"message": "Bad credentials"})
        return httpx.Response(500)  # pragma: no cover

    from backend.src.core.git_ops import PullRequestOpError

    with pytest.raises(PullRequestOpError) as exc_info:
        await open_request_pr(request=request_row, session=db_session, client_factory=_factory_for(handler))
    assert exc_info.value.reason == "github_auth"
