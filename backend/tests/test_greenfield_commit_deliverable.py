"""Tests for ``commit_deliverable`` + VerifierWorker hook (G8.2)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from backend.src.config import settings as app_settings
from backend.src.core.domain import DeliverableType, ProofState, WorkPlanCreatedBy
from backend.src.core.encryption import EncryptionManager
from backend.src.core.git_ops import CommitOpError, commit_deliverable
from backend.src.core.github import GithubClient
from backend.src.core.work_steps import WorkStepDraft, create_work_plan
from backend.src.models import Deliverable, Project, Request
from backend.src.models.project import WorkspaceType
from backend.src.workers.verifier import process_one


def _enc(value: str) -> str:
    return EncryptionManager(app_settings.encryption_key).encrypt_value(value)


def _factory_for(handler: Callable[[httpx.Request], httpx.Response]):
    def _factory(token: str) -> GithubClient:  # noqa: ARG001
        transport = httpx.MockTransport(handler)
        http = httpx.AsyncClient(base_url="https://api.github.com", transport=transport)
        return GithubClient(token=token, client=http)

    return _factory


async def _seed_project_request_step_deliverable(
    db_session,
    tenant_id: uuid.UUID,
    *,
    tmp_path: Path,
    artifact_files: dict[str, str],
    repo_url: str = "https://github.com/acme/widget",
    branch: str = "main",
    token: str | None = "ghp_secret",
) -> tuple[Project, Request, Deliverable]:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    for rel, body in artifact_files.items():
        p = workspace / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    project = Project(
        tenant_id=tenant_id,
        name="P",
        description="",
        workspace_type=WorkspaceType.local_import,
        workspace_dir=str(workspace),
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
    from backend.src.models import WorkStep

    step = (await db_session.execute(select(WorkStep).where(WorkStep.plan_id == plan.id))).scalar_one()
    deliverable = Deliverable(
        tenant_id=tenant_id,
        project_id=project.id,
        request_id=request_row.id,
        work_step_id=step.id,
        type=DeliverableType.code,
        title="Add health route",
        summary="Returns 200 ok for /healthz",
        artifact_refs=[{"path": rel} for rel in artifact_files],
    )
    db_session.add(deliverable)
    await db_session.commit()
    await db_session.refresh(deliverable)
    return project, request_row, deliverable


def _branch_aware_handler(branch_name: str, *, base_commit_sha: str = "base-sha"):
    """Default handler that satisfies the ensure_request_branch happy
    path plus every Contents API call with sequential commit SHAs.
    """
    counter = {"i": 0}
    last_request: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        last_request["req"] = request
        path = request.url.path
        if request.method == "GET" and path.endswith(f"/git/refs/heads/{branch_name}"):
            return httpx.Response(
                200,
                json={"ref": f"refs/heads/{branch_name}", "object": {"sha": "branch-sha", "type": "commit"}},
            )
        if request.method == "GET" and "/git/refs/heads/" in path:
            # Base-branch lookup — return success so create_ref proceeds.
            return httpx.Response(
                200,
                json={"ref": "refs/heads/main", "object": {"sha": base_commit_sha, "type": "commit"}},
            )
        if request.method == "POST" and path.endswith("/git/refs"):
            return httpx.Response(
                201,
                json={"ref": f"refs/heads/{branch_name}", "object": {"sha": "branch-sha", "type": "commit"}},
            )
        if request.method == "GET" and "/contents/" in path:
            # File does not exist yet — create path on PUT.
            return httpx.Response(404, json={"message": "Not Found"})
        if request.method == "PUT" and "/contents/" in path:
            counter["i"] += 1
            return httpx.Response(
                201,
                json={
                    "content": {"sha": f"blob-{counter['i']}"},
                    "commit": {"sha": f"commit-{counter['i']}"},
                },
            )
        return httpx.Response(500, json={"message": "unexpected"})

    return handler, last_request


@pytest.mark.asyncio
async def test_commit_deliverable_creates_files_and_stamps_sha(
    db_session, mock_tenant_id, seeded_tenant, tmp_path
) -> None:
    _, request_row, deliverable = await _seed_project_request_step_deliverable(
        db_session,
        mock_tenant_id,
        tmp_path=tmp_path,
        artifact_files={"src/api.py": "def healthz():\n    return 'ok'\n"},
    )
    branch_name = f"bsnexus/req-{request_row.id}"
    handler, _ = _branch_aware_handler(branch_name)

    result = await commit_deliverable(
        deliverable=deliverable,
        session=db_session,
        client_factory=_factory_for(handler),
    )
    assert result.branch_name == branch_name
    assert result.commit_sha == "commit-1"
    assert result.paths_committed == ["src/api.py"]
    assert result.paths_skipped == []
    # commit_deliverable does NOT commit the txn — the caller does.
    # The in-memory attribute is set; we verify that directly.
    assert deliverable.commit_sha == "commit-1"


@pytest.mark.asyncio
async def test_commit_deliverable_updates_existing_file_with_sha(
    db_session, mock_tenant_id, seeded_tenant, tmp_path
) -> None:
    _, request_row, deliverable = await _seed_project_request_step_deliverable(
        db_session,
        mock_tenant_id,
        tmp_path=tmp_path,
        artifact_files={"src/api.py": "updated content\n"},
    )
    branch_name = f"bsnexus/req-{request_row.id}"
    captured_put_bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path.endswith(f"/git/refs/heads/{branch_name}"):
            return httpx.Response(
                200,
                json={"ref": f"refs/heads/{branch_name}", "object": {"sha": "branch-sha", "type": "commit"}},
            )
        if request.method == "GET" and "/contents/" in path:
            # File exists; return its blob SHA so the PUT becomes an update.
            return httpx.Response(200, json={"sha": "old-blob", "path": "src/api.py"})
        if request.method == "PUT" and "/contents/" in path:
            captured_put_bodies.append(request.content)
            return httpx.Response(
                200,
                json={
                    "content": {"sha": "new-blob"},
                    "commit": {"sha": "commit-update"},
                },
            )
        return httpx.Response(500)  # pragma: no cover

    result = await commit_deliverable(
        deliverable=deliverable,
        session=db_session,
        client_factory=_factory_for(handler),
    )
    assert result.commit_sha == "commit-update"
    assert b'"sha":"old-blob"' in captured_put_bodies[0]


@pytest.mark.asyncio
async def test_commit_deliverable_raises_no_artifacts(db_session, mock_tenant_id, seeded_tenant, tmp_path) -> None:
    _, _, deliverable = await _seed_project_request_step_deliverable(
        db_session, mock_tenant_id, tmp_path=tmp_path, artifact_files={"src/a.py": "x"}
    )
    deliverable.artifact_refs = []
    await db_session.commit()
    with pytest.raises(CommitOpError) as exc_info:
        await commit_deliverable(deliverable=deliverable, session=db_session)
    assert exc_info.value.reason == "no_artifacts"


@pytest.mark.asyncio
async def test_commit_deliverable_forwards_branch_op_repo_not_bound(
    db_session, mock_tenant_id, seeded_tenant, tmp_path
) -> None:
    _, _, deliverable = await _seed_project_request_step_deliverable(
        db_session,
        mock_tenant_id,
        tmp_path=tmp_path,
        artifact_files={"src/a.py": "x"},
        repo_url="https://github.com/acme/widget",
        token=None,
    )
    # Manually unbind the repo to trigger repo_not_bound from ensure_request_branch.
    project = await db_session.get(Project, deliverable.project_id)
    assert project is not None
    project.github_repo_url = None
    project.github_token_encrypted = None
    await db_session.commit()
    with pytest.raises(CommitOpError) as exc_info:
        await commit_deliverable(deliverable=deliverable, session=db_session)
    assert exc_info.value.reason == "repo_not_bound"


@pytest.mark.asyncio
async def test_commit_deliverable_skips_missing_workspace_file_but_continues(
    db_session, mock_tenant_id, seeded_tenant, tmp_path
) -> None:
    _, request_row, deliverable = await _seed_project_request_step_deliverable(
        db_session,
        mock_tenant_id,
        tmp_path=tmp_path,
        artifact_files={"src/a.py": "present\n"},
    )
    # Append a missing artifact ref to deliverable_refs.
    deliverable.artifact_refs = [
        {"path": "src/a.py"},
        {"path": "src/ghost.py"},  # not present on disk
    ]
    await db_session.commit()
    branch_name = f"bsnexus/req-{request_row.id}"
    handler, _ = _branch_aware_handler(branch_name)

    result = await commit_deliverable(
        deliverable=deliverable,
        session=db_session,
        client_factory=_factory_for(handler),
    )
    assert "src/a.py" in result.paths_committed
    assert "src/ghost.py" in result.paths_skipped


@pytest.mark.asyncio
async def test_commit_deliverable_raises_github_unavailable_when_all_fail(
    db_session, mock_tenant_id, seeded_tenant, tmp_path
) -> None:
    _, request_row, deliverable = await _seed_project_request_step_deliverable(
        db_session,
        mock_tenant_id,
        tmp_path=tmp_path,
        artifact_files={"src/a.py": "x\n"},
    )
    branch_name = f"bsnexus/req-{request_row.id}"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path.endswith(f"/git/refs/heads/{branch_name}"):
            return httpx.Response(
                200,
                json={"ref": f"refs/heads/{branch_name}", "object": {"sha": "branch-sha", "type": "commit"}},
            )
        if request.method == "GET" and "/contents/" in path:
            return httpx.Response(404, json={"message": "Not Found"})
        if request.method == "PUT":
            return httpx.Response(500, json={"message": "upstream broke"})
        return httpx.Response(500)  # pragma: no cover

    with pytest.raises(CommitOpError) as exc_info:
        await commit_deliverable(
            deliverable=deliverable,
            session=db_session,
            client_factory=_factory_for(handler),
        )
    assert exc_info.value.reason == "github_unavailable"


@pytest.mark.asyncio
async def test_commit_deliverable_rejects_path_outside_workspace(
    db_session, mock_tenant_id, seeded_tenant, tmp_path
) -> None:
    _, request_row, deliverable = await _seed_project_request_step_deliverable(
        db_session,
        mock_tenant_id,
        tmp_path=tmp_path,
        artifact_files={"src/a.py": "x\n"},
    )
    deliverable.artifact_refs = [{"path": "../escape.py"}]
    await db_session.commit()
    branch_name = f"bsnexus/req-{request_row.id}"
    handler, _ = _branch_aware_handler(branch_name)
    with pytest.raises(CommitOpError) as exc_info:
        await commit_deliverable(
            deliverable=deliverable,
            session=db_session,
            client_factory=_factory_for(handler),
        )
    # The traversal path is skipped silently; with no valid artifact
    # the function raises github_unavailable so the soft-fail handler
    # in the worker leaves commit_sha None.
    assert exc_info.value.reason == "github_unavailable"


# ───────────── VerifierWorker integration (G8.2 hook) ─────────────


@pytest.mark.asyncio
async def test_worker_commits_on_verified_and_stamps_sha(
    db_session, mock_tenant_id, seeded_tenant, tmp_path, monkeypatch
) -> None:
    _, request_row, deliverable = await _seed_project_request_step_deliverable(
        db_session,
        mock_tenant_id,
        tmp_path=tmp_path,
        artifact_files={"src/api.py": "def healthz():\n    return 'ok'\n"},
    )
    branch_name = f"bsnexus/req-{request_row.id}"
    handler, _ = _branch_aware_handler(branch_name)

    # Force the verifier to declare verified without running a real pytest:
    # monkeypatch run_verification to flip proof_state directly.
    async def fake_run_verification(*, deliverable, session, **_kwargs) -> None:
        deliverable.proof_state = ProofState.verified
        # Real run_verification commits before returning; the worker
        # test relies on that for state to survive a later refresh.
        await session.commit()

    monkeypatch.setattr("backend.src.workers.verifier.run_verification", fake_run_verification)

    events: list[tuple[str, str, dict]] = []

    async def publish_event(project_id: str, event_type: str, payload: dict) -> None:
        events.append((project_id, event_type, payload))

    await process_one(
        deliverable_id=deliverable.id,
        tenant_id=mock_tenant_id,
        session=db_session,
        publish_event=publish_event,
        github_client_factory=_factory_for(handler),
    )
    await db_session.refresh(deliverable)
    assert deliverable.proof_state == ProofState.verified
    assert deliverable.commit_sha == "commit-1"
    assert len(events) == 1
    assert events[0][1] == "deliverable_proof"
    assert events[0][2]["commit_sha"] == "commit-1"


@pytest.mark.asyncio
async def test_worker_keeps_verified_on_commit_failure(
    db_session, mock_tenant_id, seeded_tenant, tmp_path, monkeypatch
) -> None:
    """Commit op failures must NOT revert ``proof_state=verified``."""
    _, _, deliverable = await _seed_project_request_step_deliverable(
        db_session,
        mock_tenant_id,
        tmp_path=tmp_path,
        artifact_files={"src/api.py": "x\n"},
    )

    async def fake_run_verification(*, deliverable, session, **_kwargs) -> None:
        deliverable.proof_state = ProofState.verified
        # Real run_verification commits before returning; the worker
        # test relies on that for state to survive a later refresh.
        await session.commit()

    monkeypatch.setattr("backend.src.workers.verifier.run_verification", fake_run_verification)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"message": "down"})

    await process_one(
        deliverable_id=deliverable.id,
        tenant_id=mock_tenant_id,
        session=db_session,
        publish_event=None,
        github_client_factory=_factory_for(handler),
    )
    await db_session.refresh(deliverable)
    assert deliverable.proof_state == ProofState.verified
    assert deliverable.commit_sha is None


@pytest.mark.asyncio
async def test_worker_skips_commit_when_repo_not_bound(
    db_session, mock_tenant_id, seeded_tenant, tmp_path, monkeypatch
) -> None:
    _, _, deliverable = await _seed_project_request_step_deliverable(
        db_session,
        mock_tenant_id,
        tmp_path=tmp_path,
        artifact_files={"src/api.py": "x\n"},
        repo_url="https://github.com/acme/widget",
        token=None,
    )
    project = await db_session.get(Project, deliverable.project_id)
    assert project is not None
    project.github_repo_url = None
    project.github_token_encrypted = None
    await db_session.commit()

    async def fake_run_verification(*, deliverable, session, **_kwargs) -> None:
        deliverable.proof_state = ProofState.verified
        # Real run_verification commits before returning; the worker
        # test relies on that for state to survive a later refresh.
        await session.commit()

    monkeypatch.setattr("backend.src.workers.verifier.run_verification", fake_run_verification)

    factory_was_called: dict[str, bool] = {"called": False}

    def factory(_token: str) -> GithubClient:
        factory_was_called["called"] = True
        raise AssertionError("commit should be skipped when no repo binding")

    await process_one(
        deliverable_id=deliverable.id,
        tenant_id=mock_tenant_id,
        session=db_session,
        publish_event=None,
        github_client_factory=factory,
    )
    await db_session.refresh(deliverable)
    assert deliverable.proof_state == ProofState.verified
    assert deliverable.commit_sha is None
    assert factory_was_called["called"] is False
