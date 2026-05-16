"""Verification Contract P2 — LLM-as-judge tests.

Covers ``judge_criteria`` (the one verifier-LLM call grading declared
criteria) and contract-driven ``run_verification`` executing ``judge``
checks. The judge executor is always a stub — no real LLM call.
"""

from __future__ import annotations

import json
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
    ProofState,
)
from backend.src.core.verification import run_verification
from backend.src.core.verification_judge import JudgeContext, judge_criteria
from backend.src.models import Deliverable, Project, Request, VerificationAspect


@dataclass
class _StubJudgeExecutor:
    """Executor stub for the judge — returns a canned reply string, or
    raises ``raise_exc``. Records the messages it was called with."""

    reply: str = "{}"
    raise_exc: Exception | None = None
    captured: list[list[dict[str, Any]]] = field(default_factory=list)

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
        self.captured.append([dict(m) for m in messages])
        if self.raise_exc is not None:
            raise self.raise_exc
        return {
            "output_type": "text",
            "output_ref": self.reply,
            "actual_cost_cents": 0,
            "finish_reason": "stop",
            "tool_calls": None,
        }


def _verdicts(*pairs: tuple[str, bool]) -> str:
    return json.dumps({"verdicts": [{"criterion": c, "pass": p, "reason": "because"} for c, p in pairs]})


def _judge(executor: _StubJudgeExecutor) -> JudgeContext:
    return JudgeContext(executor=executor, model="stub", metadata={"tenant_id": "t", "run_id": "judge:x"})


# ───────────────────────────── judge_criteria ─────────────────────────────


@pytest.mark.asyncio
async def test_judge_all_pass(tmp_path):
    (tmp_path / "README.md").write_text("# Project\nUsage docs here.\n")
    executor = _StubJudgeExecutor(reply=_verdicts(("has docs", True), ("has usage", True)))
    status, summary = await judge_criteria(
        criteria=("has docs", "has usage"),
        workspace_root=tmp_path,
        judge=_judge(executor),
    )
    assert status == ProofAspectStatus.passed
    assert "2" in summary


@pytest.mark.asyncio
async def test_judge_one_fails(tmp_path):
    (tmp_path / "README.md").write_text("# Project\n")
    executor = _StubJudgeExecutor(reply=_verdicts(("has docs", True), ("has usage", False)))
    status, summary = await judge_criteria(
        criteria=("has docs", "has usage"),
        workspace_root=tmp_path,
        judge=_judge(executor),
    )
    assert status == ProofAspectStatus.failed
    assert "has usage" in summary


@pytest.mark.asyncio
async def test_judge_executor_failure_is_error(tmp_path):
    executor = _StubJudgeExecutor(raise_exc=RuntimeError("llm down"))
    status, summary = await judge_criteria(criteria=("x",), workspace_root=tmp_path, judge=_judge(executor))
    assert status == ProofAspectStatus.error


@pytest.mark.asyncio
async def test_judge_unparseable_reply_is_error(tmp_path):
    executor = _StubJudgeExecutor(reply="I think it looks great honestly")
    status, summary = await judge_criteria(criteria=("x",), workspace_root=tmp_path, judge=_judge(executor))
    assert status == ProofAspectStatus.error


@pytest.mark.asyncio
async def test_judge_wrong_verdict_count_is_error(tmp_path):
    # Two criteria but the judge returned one verdict.
    executor = _StubJudgeExecutor(reply=_verdicts(("x", True)))
    status, summary = await judge_criteria(criteria=("x", "y"), workspace_root=tmp_path, judge=_judge(executor))
    assert status == ProofAspectStatus.error


@pytest.mark.asyncio
async def test_judge_prompt_includes_files_and_criteria(tmp_path):
    (tmp_path / "app.py").write_text("VALUE = 42\n")
    executor = _StubJudgeExecutor(reply=_verdicts(("defines VALUE", True)))
    await judge_criteria(criteria=("defines VALUE",), workspace_root=tmp_path, judge=_judge(executor))
    user_msg = next(m["content"] for m in executor.captured[0] if m["role"] == "user")
    assert "VALUE = 42" in user_msg
    assert "defines VALUE" in user_msg


@pytest.mark.asyncio
async def test_judge_skips_dotfiles(tmp_path):
    (tmp_path / "app.py").write_text("CODE = 1\n")
    (tmp_path / ".secret").write_text("TOPSECRET\n")
    executor = _StubJudgeExecutor(reply=_verdicts(("x", True)))
    await judge_criteria(criteria=("x",), workspace_root=tmp_path, judge=_judge(executor))
    user_msg = next(m["content"] for m in executor.captured[0] if m["role"] == "user")
    assert "TOPSECRET" not in user_msg


# ──────────────────── run_verification with judge checks ──────────────────


async def _make_deliverable(db_session, tenant_id: uuid.UUID) -> Deliverable:
    project = Project(tenant_id=tenant_id, name="J", description="")
    db_session.add(project)
    await db_session.flush()
    request = Request(tenant_id=tenant_id, project_id=project.id, intent="write docs")
    db_session.add(request)
    await db_session.flush()
    deliverable = Deliverable(
        tenant_id=tenant_id,
        project_id=project.id,
        request_id=request.id,
        type=DeliverableType.code,
        title="Docs",
        artifact_refs=[{"path": "README.md"}],
        status=DeliverableStatus.draft,
        proof_state=ProofState.verification_missing,
    )
    db_session.add(deliverable)
    await db_session.commit()
    await db_session.refresh(deliverable)
    return deliverable


@pytest.mark.asyncio
async def test_run_verification_judge_pass_verifies(db_session, mock_tenant_id, seeded_tenant, tmp_path):
    (tmp_path / "README.md").write_text("# Project\nFull usage docs.\n")
    deliverable = await _make_deliverable(db_session, mock_tenant_id)
    executor = _StubJudgeExecutor(reply=_verdicts(("README documents usage", True)))
    await run_verification(
        deliverable=deliverable,
        workspace_root=tmp_path,
        session=db_session,
        verification_contract={"checks": [{"kind": "judge", "criteria": ["README documents usage"]}]},
        judge=_judge(executor),
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
    assert aspects[0].status == ProofAspectStatus.passed


@pytest.mark.asyncio
async def test_run_verification_judge_fail_blocks(db_session, mock_tenant_id, seeded_tenant, tmp_path):
    (tmp_path / "README.md").write_text("# Project\n")
    deliverable = await _make_deliverable(db_session, mock_tenant_id)
    executor = _StubJudgeExecutor(reply=_verdicts(("README documents usage", False)))
    await run_verification(
        deliverable=deliverable,
        workspace_root=tmp_path,
        session=db_session,
        verification_contract={"checks": [{"kind": "judge", "criteria": ["README documents usage"]}]},
        judge=_judge(executor),
    )
    assert deliverable.proof_state == ProofState.verification_failed


@pytest.mark.asyncio
async def test_run_verification_judge_without_executor_falls_to_human_review(
    db_session, mock_tenant_id, seeded_tenant, tmp_path
):
    """No JudgeContext (no executor) → the judge check is skipped and
    the deliverable cannot auto-verify."""
    (tmp_path / "README.md").write_text("# Project\n")
    deliverable = await _make_deliverable(db_session, mock_tenant_id)
    await run_verification(
        deliverable=deliverable,
        workspace_root=tmp_path,
        session=db_session,
        verification_contract={"checks": [{"kind": "judge", "criteria": ["docs are complete"]}]},
        judge=None,
    )
    assert deliverable.proof_state == ProofState.human_review_required


@pytest.mark.asyncio
async def test_run_verification_mixed_command_and_judge(db_session, mock_tenant_id, seeded_tenant, tmp_path):
    """A command check + a judge check, both passing → verified."""
    (tmp_path / "README.md").write_text("# Project\nDocs.\n")
    deliverable = await _make_deliverable(db_session, mock_tenant_id)
    executor = _StubJudgeExecutor(reply=_verdicts(("docs exist", True)))
    await run_verification(
        deliverable=deliverable,
        workspace_root=tmp_path,
        session=db_session,
        verification_contract={
            "checks": [
                {"kind": "command", "command": "true"},
                {"kind": "judge", "criteria": ["docs exist"]},
            ]
        },
        judge=_judge(executor),
    )
    assert deliverable.proof_state == ProofState.verified
