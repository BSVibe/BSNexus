"""Tests for ``build_deliverable_diff_url`` + wire surfacing (G8.5)."""

from __future__ import annotations

import uuid

import pytest

from backend.src.core.domain import DeliverableType, ProofState
from backend.src.core.git_ops import build_deliverable_diff_url
from backend.src.models import Deliverable, Project
from backend.src.models.project import WorkspaceType


def _project(*, repo: str | None = "https://github.com/acme/widget") -> Project:
    return Project(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        name="P",
        description="",
        workspace_type=WorkspaceType.server_managed,
        github_repo_url=repo,
        github_branch="main",
    )


def _deliverable(*, commit_sha: str | None = "abc123def456") -> Deliverable:
    return Deliverable(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        type=DeliverableType.code,
        title="Test",
        artifact_refs=[],
        proof_state=ProofState.verified,
        commit_sha=commit_sha,
    )


def test_returns_commit_url_when_repo_and_commit_present() -> None:
    project = _project()
    deliverable = _deliverable()
    url = build_deliverable_diff_url(project=project, deliverable=deliverable)
    assert url == "https://github.com/acme/widget/commit/abc123def456"


def test_returns_none_when_repo_not_bound() -> None:
    project = _project(repo=None)
    deliverable = _deliverable()
    assert build_deliverable_diff_url(project=project, deliverable=deliverable) is None


def test_returns_none_when_commit_sha_missing() -> None:
    project = _project()
    deliverable = _deliverable(commit_sha=None)
    assert build_deliverable_diff_url(project=project, deliverable=deliverable) is None


def test_returns_none_on_unparseable_repo_url() -> None:
    project = _project(repo="git@github.com:acme/widget.git")
    deliverable = _deliverable()
    assert build_deliverable_diff_url(project=project, deliverable=deliverable) is None


def test_strips_dot_git_suffix() -> None:
    project = _project(repo="https://github.com/acme/widget.git")
    deliverable = _deliverable(commit_sha="feedface")
    url = build_deliverable_diff_url(project=project, deliverable=deliverable)
    assert url == "https://github.com/acme/widget/commit/feedface"


# ─────────────────── Wire surface (API + brief) ───────────────────


@pytest.mark.asyncio
async def test_deliverable_response_includes_diff_url_when_committed(client, db_session, mock_tenant_id, seeded_tenant):
    from backend.src.api.deliverables import _deliverable_response

    project = Project(
        tenant_id=mock_tenant_id,
        name="P",
        description="",
        workspace_type=WorkspaceType.server_managed,
        github_repo_url="https://github.com/acme/widget",
    )
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)
    deliverable = Deliverable(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        type=DeliverableType.code,
        title="Add /healthz",
        artifact_refs=[],
        proof_state=ProofState.verified,
        commit_sha="cafebabe1234",
    )
    db_session.add(deliverable)
    await db_session.commit()
    await db_session.refresh(deliverable)

    payload = await _deliverable_response(db_session, deliverable)
    assert payload["diff_url"] == "https://github.com/acme/widget/commit/cafebabe1234"


@pytest.mark.asyncio
async def test_deliverable_response_diff_url_null_when_no_repo(db_session, mock_tenant_id, seeded_tenant):
    from backend.src.api.deliverables import _deliverable_response

    project = Project(
        tenant_id=mock_tenant_id,
        name="P",
        description="",
        workspace_type=WorkspaceType.server_managed,
        github_repo_url=None,
    )
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)
    deliverable = Deliverable(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        type=DeliverableType.code,
        title="No repo",
        artifact_refs=[],
        proof_state=ProofState.verified,
        commit_sha="abc",
    )
    db_session.add(deliverable)
    await db_session.commit()
    await db_session.refresh(deliverable)
    payload = await _deliverable_response(db_session, deliverable)
    assert payload["diff_url"] is None


@pytest.mark.asyncio
async def test_brief_shipped_card_includes_diff_url(db_session, mock_tenant_id, seeded_tenant):
    from backend.src.core.brief import _deliverable_card

    project = Project(
        tenant_id=mock_tenant_id,
        name="P",
        description="",
        workspace_type=WorkspaceType.server_managed,
        github_repo_url="https://github.com/acme/widget",
    )
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)
    deliverable = Deliverable(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        type=DeliverableType.code,
        title="Brief card",
        artifact_refs=[],
        proof_state=ProofState.verified,
        commit_sha="1234567890ab",
    )
    db_session.add(deliverable)
    await db_session.commit()
    await db_session.refresh(deliverable)
    card = await _deliverable_card(db_session, deliverable)
    assert card["commit_sha"] == "1234567890ab"
    assert card["diff_url"] == "https://github.com/acme/widget/commit/1234567890ab"
