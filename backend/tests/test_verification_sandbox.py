"""Part B / PR 6 — verification runs declared command checks in the
project sandbox.

When a SandboxSession is supplied, declared `command` aspects execute
inside the sandbox (the work phase's toolchain). Missing toolchain
(exit 127) → skipped, never a false fail. Host path (no session) is
covered by test_verification_contract_execution.py.
"""

from __future__ import annotations

import uuid

import pytest

from backend.src.core.domain import (
    DeliverableStatus,
    DeliverableType,
    ProofAspectStatus,
    ProofAspectType,
    ProofState,
)
from backend.src.core.sandbox import SandboxResult
from backend.src.core.verification import AspectSpec, _run_aspect_in_sandbox, run_verification
from backend.src.models import Deliverable, Project, Request


class FakeSandboxSession:
    """Records exec calls; returns a scripted result keyed by substring."""

    def __init__(self) -> None:
        self.exec_calls: list[tuple[str, bool]] = []
        self.default = SandboxResult(exit_code=0, stdout="ok", stderr="", timed_out=False)
        self.scripted: dict[str, SandboxResult] = {}

    @property
    def workspace_mount(self) -> str:
        return "/work"

    async def exec(self, command: str, *, timeout_s: float, shell: bool = False) -> SandboxResult:
        self.exec_calls.append((command, shell))
        for needle, result in self.scripted.items():
            if needle in command:
                return result
        return self.default

    async def read_file(self, rel_path: str, max_bytes: int) -> bytes:  # pragma: no cover
        raise NotImplementedError

    async def write_file(self, rel_path: str, content: bytes) -> None:  # pragma: no cover
        raise NotImplementedError

    async def list_dir(self, rel_path: str) -> list[str]:  # pragma: no cover
        raise NotImplementedError


def _cmd_spec(command: str) -> AspectSpec:
    return AspectSpec(
        aspect_type=ProofAspectType.declared_command,
        commands=(("sh", "-c", command),),
        timeout_s=60,
        blocking=True,
    )


async def test_run_aspect_in_sandbox_passes_on_exit_zero() -> None:
    fake = FakeSandboxSession()
    status, summary, exit_code = await _run_aspect_in_sandbox(_cmd_spec("pytest"), fake)
    assert status == ProofAspectStatus.passed
    assert exit_code == 0
    # routed through the sandbox shell, command unwrapped from ("sh","-c",X)
    assert fake.exec_calls == [("pytest", True)]


async def test_run_aspect_in_sandbox_fails_on_nonzero_exit() -> None:
    fake = FakeSandboxSession()
    fake.scripted["pytest"] = SandboxResult(exit_code=1, stdout="", stderr="boom", timed_out=False)
    status, _summary, exit_code = await _run_aspect_in_sandbox(_cmd_spec("pytest"), fake)
    assert status == ProofAspectStatus.failed
    assert exit_code == 1


async def test_run_aspect_in_sandbox_skips_on_missing_toolchain() -> None:
    fake = FakeSandboxSession()
    fake.scripted["cargo"] = SandboxResult(exit_code=127, stdout="", stderr="not found", timed_out=False)
    status, summary, exit_code = await _run_aspect_in_sandbox(_cmd_spec("cargo test"), fake)
    assert status == ProofAspectStatus.skipped  # missing toolchain, never a false fail
    assert exit_code == 127


async def test_run_aspect_in_sandbox_fails_on_timeout() -> None:
    fake = FakeSandboxSession()
    fake.scripted["sleep"] = SandboxResult(exit_code=None, stdout="", stderr="", timed_out=True)
    status, _summary, _exit = await _run_aspect_in_sandbox(_cmd_spec("sleep 999"), fake)
    assert status == ProofAspectStatus.failed


async def _make_deliverable(db_session, tenant_id: uuid.UUID) -> Deliverable:
    project = Project(tenant_id=tenant_id, name="VS", description="")
    db_session.add(project)
    await db_session.flush()
    request = Request(tenant_id=tenant_id, project_id=project.id, intent="work")
    db_session.add(request)
    await db_session.flush()
    deliverable = Deliverable(
        tenant_id=tenant_id,
        project_id=project.id,
        request_id=request.id,
        type=DeliverableType.code,
        title="Impl",
        artifact_refs=[{"path": "app.py"}],
        status=DeliverableStatus.draft,
        proof_state=ProofState.verification_missing,
    )
    db_session.add(deliverable)
    await db_session.commit()
    await db_session.refresh(deliverable)
    return deliverable


@pytest.mark.asyncio
async def test_run_verification_routes_command_checks_through_sandbox(
    db_session, mock_tenant_id, seeded_tenant, tmp_path
):
    deliverable = await _make_deliverable(db_session, mock_tenant_id)
    fake = FakeSandboxSession()  # every command exits 0
    await run_verification(
        deliverable=deliverable,
        workspace_root=tmp_path,
        session=db_session,
        verification_contract={"checks": [{"kind": "command", "command": "pytest -q", "rationale": "tests"}]},
        sandbox_session=fake,
    )
    assert deliverable.proof_state == ProofState.verified
    # the command actually ran in the sandbox, not host-side
    assert ("pytest -q", True) in fake.exec_calls
