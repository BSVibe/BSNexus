"""Verification Contract — P1 execution tests.

Covers the declare_verification tool, contract-driven
``run_verification``, and the dispatcher persisting the declared
contract onto ``RunAttempt.verification_contract``.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy import select

from backend.src.core.domain import (
    DeliverableStatus,
    DeliverableType,
    ProofAspectStatus,
    ProofAspectType,
    ProofState,
    WorkPlanCreatedBy,
)
from backend.src.core.run_attempt_executor import dispatch_run_attempt
from backend.src.core.tools import ToolError, ToolRegistry
from backend.src.core.verification import run_verification
from backend.src.core.work_steps import WorkStepDraft, create_work_plan
from backend.src.models import Deliverable, Project, Request, RunAttempt, VerificationAspect, WorkStep


# ───────────────────────── declare_verification tool ─────────────────────


@pytest.mark.asyncio
async def test_declare_verification_records_contract(tmp_path):
    registry = ToolRegistry(workspace_dir=tmp_path)
    assert registry.declared_contract is None
    assert registry.has("declare_verification")

    out = await registry.invoke(
        "declare_verification",
        {
            "checks": [
                {"kind": "command", "command": "uv run pytest", "rationale": "unit tests"},
                {"kind": "judge", "criteria": ["README is complete"], "rationale": "docs"},
            ]
        },
    )
    assert "recorded" in out
    assert registry.declared_contract == {
        "checks": [
            {"kind": "command", "command": "uv run pytest", "rationale": "unit tests"},
            {"kind": "judge", "criteria": ["README is complete"], "rationale": "docs"},
        ]
    }


@pytest.mark.asyncio
async def test_declare_verification_rejects_empty(tmp_path):
    registry = ToolRegistry(workspace_dir=tmp_path)
    with pytest.raises(ToolError):
        await registry.invoke("declare_verification", {"checks": []})
    with pytest.raises(ToolError):
        await registry.invoke("declare_verification", {"checks": [{"kind": "command", "command": ""}]})
    assert registry.declared_contract is None


@pytest.mark.asyncio
async def test_declare_verification_appears_in_work_phase_schema(tmp_path):
    registry = ToolRegistry(workspace_dir=tmp_path)
    schema = registry.schema_for(["file_write", "declare_verification"])
    names = {entry["function"]["name"] for entry in schema}
    assert "declare_verification" in names


def test_declare_verification_description_warns_against_compile_only_checks(tmp_path):
    """Phase A / A1 — the tool description must steer the model away
    from a weak contract: a check that only compiles or imports a file
    does not verify behaviour; when the step has tests the contract
    must RUN them. Includes the weak ``py_compile`` example as the
    canonical anti-pattern."""
    registry = ToolRegistry(workspace_dir=tmp_path)
    description = registry.schema_for(["declare_verification"])[0]["function"]["description"]
    assert "compiles or imports" in description
    assert "RUNS the test runner" in description
    assert "py_compile" in description


# ───────────────────────── contract-driven verification ──────────────────


async def _make_deliverable(db_session, tenant_id: uuid.UUID) -> Deliverable:
    project = Project(tenant_id=tenant_id, name="VC", description="")
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
async def test_run_verification_passing_command_contract(db_session, mock_tenant_id, seeded_tenant, tmp_path):
    deliverable = await _make_deliverable(db_session, mock_tenant_id)
    await run_verification(
        deliverable=deliverable,
        workspace_root=tmp_path,
        session=db_session,
        verification_contract={"checks": [{"kind": "command", "command": "true", "rationale": "ok"}]},
    )
    assert deliverable.proof_state == ProofState.verified
    aspects = (
        (
            await db_session.execute(
                select(VerificationAspect).where(VerificationAspect.deliverable_id == deliverable.id)
            )
        )
        .scalars()
        .all()
    )
    assert [a.aspect_type for a in aspects] == [ProofAspectType.declared_command]
    assert aspects[0].status == ProofAspectStatus.passed


@pytest.mark.asyncio
async def test_run_verification_failing_command_contract(db_session, mock_tenant_id, seeded_tenant, tmp_path):
    deliverable = await _make_deliverable(db_session, mock_tenant_id)
    await run_verification(
        deliverable=deliverable,
        workspace_root=tmp_path,
        session=db_session,
        verification_contract={"checks": [{"kind": "command", "command": "false", "rationale": "fails"}]},
    )
    assert deliverable.proof_state == ProofState.verification_failed


@pytest.mark.asyncio
async def test_run_verification_judge_check_falls_to_human_review(db_session, mock_tenant_id, seeded_tenant, tmp_path):
    """P1: a judge check cannot execute yet — even alongside a passing
    command check the deliverable must not auto-verify."""
    deliverable = await _make_deliverable(db_session, mock_tenant_id)
    await run_verification(
        deliverable=deliverable,
        workspace_root=tmp_path,
        session=db_session,
        verification_contract={
            "checks": [
                {"kind": "command", "command": "true", "rationale": "ok"},
                {"kind": "judge", "criteria": ["doc is complete"], "rationale": "docs"},
            ]
        },
    )
    assert deliverable.proof_state == ProofState.human_review_required


@pytest.mark.asyncio
async def test_run_verification_no_contract_uses_heuristic_fallback(
    db_session, mock_tenant_id, seeded_tenant, tmp_path
):
    """No declared contract → heuristic fallback. An empty workspace
    matches no heuristic aspect → human_review_required."""
    deliverable = await _make_deliverable(db_session, mock_tenant_id)
    await run_verification(
        deliverable=deliverable,
        workspace_root=tmp_path,
        session=db_session,
        verification_contract=None,
    )
    assert deliverable.proof_state == ProofState.human_review_required


# ───────────────────────── dispatcher persists the contract ──────────────


@dataclass
class _ScriptedExecutor:
    tool_call_scripts: list[list[dict[str, Any]]] = field(default_factory=list)
    final_text: str = "Done."

    async def execute(
        self,
        *,
        messages: list[dict[str, Any]],
        metadata: dict[str, Any],
        model: str,
        workspace_dir: str | None = None,
        mcp_servers: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        on_chunk: Callable[[str], Awaitable[None]] | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        if self.tool_call_scripts:
            return {
                "output_type": "text",
                "output_ref": "",
                "actual_cost_cents": 0,
                "finish_reason": "tool_calls",
                "tool_calls": self.tool_call_scripts.pop(0),
            }
        return {
            "output_type": "text",
            "output_ref": self.final_text,
            "actual_cost_cents": 0,
            "finish_reason": "stop",
            "tool_calls": None,
        }


async def _seed_request_with_step(db_session, tenant_id) -> tuple[Request, WorkStep]:
    project = Project(tenant_id=tenant_id, name="VC dispatch", description="")
    db_session.add(project)
    await db_session.flush()
    request = Request(tenant_id=tenant_id, project_id=project.id, intent="Add a parser")
    db_session.add(request)
    await db_session.commit()
    await db_session.refresh(request)
    plan = await create_work_plan(
        request=request,
        steps=[WorkStepDraft(name="Do work", objective="Write a file")],
        created_by=WorkPlanCreatedBy.system,
        session=db_session,
    )
    step = (await db_session.execute(select(WorkStep).where(WorkStep.plan_id == plan.id))).scalar_one()
    return request, step


@pytest.mark.asyncio
async def test_dispatch_persists_declared_contract_on_run_attempt(
    db_session, mock_tenant_id, seeded_tenant, mock_stream_manager, tmp_path
):
    """The work LLM calls declare_verification; the dispatcher records
    the contract on RunAttempt.verification_contract."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="0"\n')
    request, step = await _seed_request_with_step(db_session, mock_tenant_id)
    executor = _ScriptedExecutor(
        tool_call_scripts=[
            [
                {
                    "id": "c1",
                    "name": "declare_verification",
                    "arguments": {"checks": [{"kind": "command", "command": "true", "rationale": "smoke"}]},
                }
            ],
            [
                {
                    "id": "c2",
                    "name": "file_write",
                    "arguments": {"path": "src/app.py", "content": "X = 1\n"},
                }
            ],
        ],
        final_text="Declared and implemented.",
    )

    result = await dispatch_run_attempt(
        request=request,
        work_step=step,
        tenant_id=mock_tenant_id,
        session=db_session,
        stream_manager=mock_stream_manager,
        executor=executor,
        executor_kind="injected",
        model="stub-model",
        workspace_dir=tmp_path,
    )

    attempt = await db_session.get(RunAttempt, result.attempt.id)
    assert attempt is not None
    assert attempt.verification_contract == {"checks": [{"kind": "command", "command": "true", "rationale": "smoke"}]}
