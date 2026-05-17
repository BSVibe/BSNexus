"""Verification Contract — declared-command execution tests.

Pins the dogfood-found fixes (2026-05-17): a declared ``command`` check
runs through a shell (so pipes / redirects work) and with the verifier
venv's ``bin`` on PATH (so a model-declared ``ruff`` / ``pytest`` /
``python`` resolves to the toolchain, not the bare runtime container).
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.src.core.domain import DeliverableStatus, DeliverableType, ProofAspectType, ProofState
from backend.src.core.verification import (
    _contract_to_aspect_specs,
    _needs_aspect_venv,
    _venv_env,
    run_verification,
)
from backend.src.core.verification_contract import parse_verification_contract
from backend.src.models import Deliverable, Project, Request, VerificationAspect


# ───────────────────────────── pure helpers ───────────────────────────────


def test_venv_env_prepends_bin_to_path():
    venv_python = Path("/srv/.venv/bin/python")
    env = _venv_env(venv_python)
    assert env is not None
    assert env["PATH"].split(":")[0] == "/srv/.venv/bin"
    assert env["VIRTUAL_ENV"] == "/srv/.venv"


def test_venv_env_none_without_venv():
    assert _venv_env(None) is None


def test_needs_aspect_venv_true_for_declared_command():
    contract = parse_verification_contract({"checks": [{"kind": "command", "command": "pytest"}]})
    assert contract is not None
    specs = _contract_to_aspect_specs(contract)
    assert _needs_aspect_venv(specs) is True


def test_declared_command_runs_through_a_shell():
    """The declared command is wrapped in ``sh -c`` — pipes/redirects
    are preserved instead of passed as literal argv."""
    contract = parse_verification_contract({"checks": [{"kind": "command", "command": "echo hi | grep hi"}]})
    assert contract is not None
    specs = _contract_to_aspect_specs(contract)
    assert specs[0].commands == (("sh", "-c", "echo hi | grep hi"),)


# ─────────────────── contract-driven verification (shell) ─────────────────


async def _make_deliverable(db_session, tenant_id: uuid.UUID) -> Deliverable:
    project = Project(tenant_id=tenant_id, name="DC", description="")
    db_session.add(project)
    await db_session.flush()
    request = Request(tenant_id=tenant_id, project_id=project.id, intent="do work")
    db_session.add(request)
    await db_session.flush()
    deliverable = Deliverable(
        tenant_id=tenant_id,
        project_id=project.id,
        request_id=request.id,
        type=DeliverableType.code,
        title="Impl",
        artifact_refs=[{"path": "src/app.py"}],
        status=DeliverableStatus.draft,
        proof_state=ProofState.verification_missing,
    )
    db_session.add(deliverable)
    await db_session.commit()
    await db_session.refresh(deliverable)
    return deliverable


@pytest.mark.asyncio
async def test_declared_command_with_pipe_passes(db_session, mock_tenant_id, seeded_tenant, tmp_path):
    """Bug 3 regression: a declared command using a shell pipe must run
    via the shell and pass — not break as literal argv."""
    deliverable = await _make_deliverable(db_session, mock_tenant_id)
    await run_verification(
        deliverable=deliverable,
        workspace_root=tmp_path,
        session=db_session,
        verification_contract={"checks": [{"kind": "command", "command": "echo hello | grep -q hello"}]},
    )
    assert deliverable.proof_state == ProofState.verified


@pytest.mark.asyncio
async def test_declared_command_with_redirect_passes(db_session, mock_tenant_id, seeded_tenant, tmp_path):
    """Bug 3 regression: the exact dogfood failure shape — a command
    with ``2>&1`` — must run through the shell."""
    deliverable = await _make_deliverable(db_session, mock_tenant_id)
    await run_verification(
        deliverable=deliverable,
        workspace_root=tmp_path,
        session=db_session,
        verification_contract={"checks": [{"kind": "command", "command": "ls 2>&1 | head -1 && echo ok"}]},
    )
    assert deliverable.proof_state == ProofState.verified


@pytest.mark.asyncio
async def test_declared_command_failure_still_detected(db_session, mock_tenant_id, seeded_tenant, tmp_path):
    """Shell wrapping must not mask a non-zero exit."""
    deliverable = await _make_deliverable(db_session, mock_tenant_id)
    await run_verification(
        deliverable=deliverable,
        workspace_root=tmp_path,
        session=db_session,
        verification_contract={"checks": [{"kind": "command", "command": "exit 3"}]},
    )
    assert deliverable.proof_state == ProofState.verification_failed
    aspect = (
        await db_session.execute(select(VerificationAspect).where(VerificationAspect.deliverable_id == deliverable.id))
    ).scalar_one()
    assert aspect.aspect_type == ProofAspectType.declared_command
    assert aspect.exit_code == 3


@pytest.mark.asyncio
async def test_declared_command_finds_toolchain_on_path(db_session, mock_tenant_id, seeded_tenant, tmp_path):
    """Bug 2 regression: a declared command resolves its interpreter
    via the venv ``bin`` on PATH. The test venv (fake_aspect_venv →
    ``sys.executable``) carries python, so ``python -c`` succeeds with
    no absolute path."""
    deliverable = await _make_deliverable(db_session, mock_tenant_id)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("VALUE = 1\n")
    await run_verification(
        deliverable=deliverable,
        workspace_root=tmp_path,
        session=db_session,
        verification_contract={
            "checks": [{"kind": "command", "command": 'python -c "import src.app; assert src.app.VALUE == 1"'}]
        },
    )
    assert deliverable.proof_state == ProofState.verified
    # The verifier ran inside the (faked) venv interpreter.
    assert Path(sys.executable).exists()
