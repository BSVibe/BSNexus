from __future__ import annotations

import sys
import uuid

import pytest
from sqlalchemy import select

from backend.src.core.domain import (
    DeliverableStatus,
    DeliverableType,
    ProofAttemptStatus,
    ProofState,
    RequestStatus,
)
from backend.src.core.proof import (
    ProofPolicyError,
    is_setup_only_command,
    run_proof_attempt,
    select_proof_policy,
)
from backend.src.core.work_steps import GreenfieldStateError, transition_request
from backend.src.models import Deliverable, Project, ProofAttempt, ProofPolicy, Request


async def _make_deliverable(
    db_session,
    tenant_id: uuid.UUID,
    *,
    deliverable_type: DeliverableType = DeliverableType.code,
) -> tuple[Request, Deliverable]:
    project = Project(tenant_id=tenant_id, name="Greenfield G4", description="")
    db_session.add(project)
    await db_session.flush()
    request = Request(
        tenant_id=tenant_id,
        project_id=project.id,
        intent="Create deterministic verifier proof",
    )
    db_session.add(request)
    await db_session.flush()
    deliverable = Deliverable(
        tenant_id=tenant_id,
        project_id=project.id,
        request_id=request.id,
        type=deliverable_type,
        title="Implementation",
        artifact_refs=["git:greenfield-g4"],
        status=DeliverableStatus.draft,
        proof_state=ProofState.verification_missing,
    )
    db_session.add(deliverable)
    await db_session.commit()
    await db_session.refresh(request)
    await db_session.refresh(deliverable)
    return request, deliverable


def test_selects_python_pytest_policy_from_workspace(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n")
    (tmp_path / "tests").mkdir()

    policy = select_proof_policy(
        workspace_root=tmp_path,
        deliverable_type=DeliverableType.code,
        changed_files=["src/app.py"],
    )

    assert policy is not None
    assert policy.verifier_type == "python_test"
    assert policy.command == ("python", "-m", "pytest")
    assert "pyproject.toml" in policy.required_refs
    assert "tests/" in policy.required_refs


def test_selects_node_test_policy_and_package_manager(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"test": "vitest run", "build": "vite build"}}')
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")

    policy = select_proof_policy(
        workspace_root=tmp_path,
        deliverable_type=DeliverableType.code,
        changed_files=["src/App.tsx"],
    )

    assert policy is not None
    assert policy.verifier_type == "node_test"
    assert policy.command == ("pnpm", "test")
    assert policy.required_refs == ("package.json", "pnpm-lock.yaml")


def test_selects_node_build_when_test_script_is_missing(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"build": "vite build"}}')

    policy = select_proof_policy(
        workspace_root=tmp_path,
        deliverable_type=DeliverableType.preview,
        changed_files=["package.json"],
    )

    assert policy is not None
    assert policy.verifier_type == "node_build"
    assert policy.command == ("npm", "run", "build")


def test_missing_policy_never_returns_verified_policy(tmp_path):
    (tmp_path / "README.md").write_text("# docs only\n")

    policy = select_proof_policy(
        workspace_root=tmp_path,
        deliverable_type=DeliverableType.doc,
        changed_files=["README.md"],
    )

    assert policy is None


@pytest.mark.parametrize(
    "command",
    [
        "pip install -r requirements.txt",
        ["python", "-m", "pip", "install", "-r", "requirements.txt"],
        ["uv", "sync"],
        ["npm", "install"],
        ["pnpm", "install"],
    ],
)
def test_setup_only_commands_are_not_verifier_proof(command):
    assert is_setup_only_command(command)
    with pytest.raises(ProofPolicyError):
        select_proof_policy(
            workspace_root=".",
            deliverable_type=DeliverableType.code,
            explicit_policy={"command": command},
        )


@pytest.mark.asyncio
async def test_run_proof_attempt_marks_deliverable_verified_from_server_command(
    tmp_path,
    db_session,
    mock_tenant_id,
    seeded_tenant,
):
    _, deliverable = await _make_deliverable(db_session, mock_tenant_id)

    attempt = await run_proof_attempt(
        deliverable=deliverable,
        workspace_root=tmp_path,
        changed_files=["src/app.py"],
        explicit_policy={
            "verifier_type": "unit_test",
            "command": [sys.executable, "-c", "raise SystemExit(0)"],
            "required_refs": ["src/app.py"],
        },
        session=db_session,
    )

    stored_policy = await db_session.get(ProofPolicy, deliverable.proof_policy_id)
    assert stored_policy is not None
    assert stored_policy.command_template == [sys.executable, "-c", "raise SystemExit(0)"]
    assert attempt.status == ProofAttemptStatus.verified
    assert attempt.exit_code == 0
    assert deliverable.proof_state == ProofState.verified
    assert deliverable.status == DeliverableStatus.review_ready


@pytest.mark.asyncio
async def test_failed_verifier_blocks_request_shipping(tmp_path, db_session, mock_tenant_id, seeded_tenant):
    request, deliverable = await _make_deliverable(db_session, mock_tenant_id)
    await transition_request(request=request, target=RequestStatus.running, session=db_session)
    await transition_request(request=request, target=RequestStatus.review_ready, session=db_session)

    attempt = await run_proof_attempt(
        deliverable=deliverable,
        workspace_root=tmp_path,
        explicit_policy={
            "verifier_type": "unit_test",
            "command": [sys.executable, "-c", "raise SystemExit(2)"],
        },
        session=db_session,
    )

    assert attempt.status == ProofAttemptStatus.failed
    assert attempt.exit_code == 2
    assert deliverable.proof_state == ProofState.verification_failed
    with pytest.raises(GreenfieldStateError):
        await transition_request(request=request, target=RequestStatus.shipped, session=db_session)


@pytest.mark.asyncio
async def test_missing_policy_requires_human_review_and_never_verifies(
    tmp_path,
    db_session,
    mock_tenant_id,
    seeded_tenant,
):
    _, deliverable = await _make_deliverable(db_session, mock_tenant_id, deliverable_type=DeliverableType.doc)

    attempt = await run_proof_attempt(
        deliverable=deliverable,
        workspace_root=tmp_path,
        changed_files=["README.md"],
        session=db_session,
    )

    attempts = (
        (await db_session.execute(select(ProofAttempt).where(ProofAttempt.deliverable_id == deliverable.id)))
        .scalars()
        .all()
    )
    assert attempts == [attempt]
    assert attempt.status == ProofAttemptStatus.human_review_required
    assert deliverable.proof_state == ProofState.human_review_required
    assert deliverable.proof_state != ProofState.verified
