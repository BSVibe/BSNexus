"""PR10 — derive verification block from observed shell_exec history.

PR8/PR9 relied on the LLM emitting a ``bsnexus-verification`` fenced
JSON block at the end of its reply. qwen3-coder:30b reliably skips
that even with strong prompts + multi-fire watchdogs (the
``local-llm-runtime-nudge-ceiling`` skill captured this finding).

PR10 pivots: ``DirectLLMAdapter`` records every successful
``shell_exec`` invocation in its ``local_tool_log``. The dispatcher
folds the log into ``run.output_ref``. ``_ensure_deliverable``
derives a verification block from the LAST successful ``shell_exec``
when the LLM didn't emit one explicitly. The LLM is no longer
required to emit a marker at all.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from backend.src.core.run_artifacts import publish_run_output
from backend.src.models import Deliverable, ExecutionRun, Project, ProofState, Request, RequestStatus, RunStatus


async def _seed_run(
    db_session,
    tenant_id: uuid.UUID,
    *,
    inline: str = "",
    local_tool_log: dict | None = None,
) -> ExecutionRun:
    project = Project(tenant_id=tenant_id, name="P", description="")
    db_session.add(project)
    await db_session.flush()
    request = Request(
        tenant_id=tenant_id,
        project_id=project.id,
        intent_summary="ship the thing",
        status=RequestStatus.open,
    )
    db_session.add(request)
    await db_session.flush()

    output_ref: dict = {"inline": inline}
    if local_tool_log is not None:
        output_ref["local_tool_log"] = local_tool_log

    run = ExecutionRun(
        tenant_id=tenant_id,
        project_id=project.id,
        request_id=request.id,
        status=RunStatus.done,
        output_type="text",
        output_ref=output_ref,
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)
    return run


@pytest.mark.asyncio
async def test_derives_verification_from_last_successful_shell_exec(
    db_session, mock_tenant_id, seeded_tenant
) -> None:
    """LLM didn't emit a fenced block, but ran ``pytest`` via
    shell_exec successfully → backend stamps a verification block
    using that command."""
    run = await _seed_run(
        db_session,
        mock_tenant_id,
        inline="Done — wrote add.py and tests/test_add.py.",
        local_tool_log={
            "written_files": [{"path": "add.py", "size": 32, "language": "python"}],
            "shell_invocations": [
                {"command": "python -m pytest tests/test_add.py -q", "exit_code": 0, "duration_ms": 220},
            ],
        },
    )
    deliverable = await publish_run_output(run, db_session)
    await db_session.commit()

    refreshed = (
        await db_session.execute(select(Deliverable).where(Deliverable.id == deliverable.id))
    ).scalar_one()
    assert refreshed.verifier_type == "software_test"
    assert refreshed.verifier_inputs is not None
    assert refreshed.verifier_inputs["command"] == ["python", "-m", "pytest", "tests/test_add.py", "-q"]


@pytest.mark.asyncio
async def test_skips_derive_when_no_successful_shell_exec(
    db_session, mock_tenant_id, seeded_tenant
) -> None:
    """LLM ran nothing successful → no verification stamp.
    proof_state defaults to ``verification_missing`` (the schema
    default), the deliverable surfaces as 'no proof' which is
    honest."""
    run = await _seed_run(
        db_session,
        mock_tenant_id,
        inline="ran a thing.",
        local_tool_log={
            "written_files": [],
            "shell_invocations": [
                {"command": "python -m pytest", "exit_code": 4, "duration_ms": 50},
            ],
        },
    )
    deliverable = await publish_run_output(run, db_session)
    await db_session.commit()

    refreshed = (
        await db_session.execute(select(Deliverable).where(Deliverable.id == deliverable.id))
    ).scalar_one()
    assert refreshed.verifier_type is None
    assert refreshed.proof_state == ProofState.verification_missing


@pytest.mark.asyncio
async def test_emitted_block_takes_precedence_over_derived(
    db_session, mock_tenant_id, seeded_tenant
) -> None:
    """If the LLM DID emit a fenced block, use that — the LLM might
    know about a more specific cwd / timeout / verifier_type than
    we can infer."""
    inline = (
        "Done.\n\n```bsnexus-verification\n"
        '{"verifier_type": "software_build", "command": ["pnpm", "build"], "cwd": "frontend"}\n'
        "```"
    )
    run = await _seed_run(
        db_session,
        mock_tenant_id,
        inline=inline,
        local_tool_log={
            "written_files": [],
            "shell_invocations": [
                {"command": "pnpm build", "exit_code": 0, "duration_ms": 1500},
            ],
        },
    )
    deliverable = await publish_run_output(run, db_session)
    await db_session.commit()

    refreshed = (
        await db_session.execute(select(Deliverable).where(Deliverable.id == deliverable.id))
    ).scalar_one()
    # Emitted-block wins: command was the explicit one, cwd resolved
    # against project workspace.
    assert refreshed.verifier_type == "software_build"
    assert refreshed.verifier_inputs["command"] == ["pnpm", "build"]
    assert "frontend" in refreshed.verifier_inputs["cwd"]


@pytest.mark.asyncio
async def test_derive_picks_last_successful_invocation(
    db_session, mock_tenant_id, seeded_tenant
) -> None:
    """Multiple shell_exec calls → use the LAST successful one as the
    verification command (the model's final 'this proves it' check)."""
    run = await _seed_run(
        db_session,
        mock_tenant_id,
        inline="ran a few things.",
        local_tool_log={
            "written_files": [],
            "shell_invocations": [
                {"command": "ls", "exit_code": 0, "duration_ms": 5},
                {"command": "python -m pytest -x", "exit_code": 1, "duration_ms": 200},
                {"command": "python -m pytest tests/test_app.py -q", "exit_code": 0, "duration_ms": 350},
            ],
        },
    )
    deliverable = await publish_run_output(run, db_session)
    await db_session.commit()

    refreshed = (
        await db_session.execute(select(Deliverable).where(Deliverable.id == deliverable.id))
    ).scalar_one()
    assert refreshed.verifier_inputs["command"] == [
        "python", "-m", "pytest", "tests/test_app.py", "-q",
    ]
