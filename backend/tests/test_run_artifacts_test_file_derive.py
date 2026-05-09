"""PR11 — derive verification from test files in workspace when LLM
didn't run shell_exec.

PR10 dogfood found qwen3-coder treats trivial code (e.g. ``add(a,b)``)
as "obviously correct" and skips ``shell_exec`` entirely. The PR10
``_attach_verification_from_local_tool_log`` derive path can't fire
when ``shell_invocations`` is empty.

PR11 extends the derive chain with a third tier: when shell history
is empty AND the LLM wrote test files (``test_*.py`` / ``tests/test_*.py``),
synthesize ``pytest <test_file>`` as the verification command. This
extends PR10's principle (LLM action over LLM output) one more step:
**workspace state** is also a valid verification source.
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
    inline: str = "wrote files",
    files_written: list[dict] | None = None,
    shells: list[dict] | None = None,
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

    output_ref: dict = {
        "inline": inline,
        "local_tool_log": {
            "written_files": files_written or [],
            "shell_invocations": shells or [],
        },
    }

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
async def test_derives_pytest_when_test_file_written_no_shell(db_session, mock_tenant_id, seeded_tenant) -> None:
    """LLM wrote a test file but didn't run pytest → backend
    synthesizes ``pytest tests/test_add.py`` as the verification."""
    run = await _seed_run(
        db_session,
        mock_tenant_id,
        files_written=[
            {"path": "add.py", "size": 30, "language": "python"},
            {"path": "tests/test_add.py", "size": 60, "language": "python"},
        ],
        shells=[],
    )
    deliverable = await publish_run_output(run, db_session)
    await db_session.commit()

    refreshed = (await db_session.execute(select(Deliverable).where(Deliverable.id == deliverable.id))).scalar_one()
    assert refreshed.verifier_type == "software_test"
    cmd = refreshed.verifier_inputs["command"]
    assert cmd[0] == "bash"
    assert cmd[1] == "-c"
    assert "pytest" in cmd[2]
    assert "tests/test_add.py" in cmd[2]
    assert refreshed.verifier_inputs.get("derived_from") == "workspace_test_files"


@pytest.mark.asyncio
async def test_skips_when_no_test_files_written(db_session, mock_tenant_id, seeded_tenant) -> None:
    """Files written but none match the test-file pattern → no
    synthesis, deliverable stays at ``verification_missing``."""
    run = await _seed_run(
        db_session,
        mock_tenant_id,
        files_written=[
            {"path": "add.py", "size": 30, "language": "python"},
            {"path": "README.md", "size": 100, "language": "markdown"},
        ],
        shells=[],
    )
    deliverable = await publish_run_output(run, db_session)
    await db_session.commit()

    refreshed = (await db_session.execute(select(Deliverable).where(Deliverable.id == deliverable.id))).scalar_one()
    assert refreshed.verifier_type is None
    assert refreshed.proof_state == ProofState.verification_missing


@pytest.mark.asyncio
async def test_shell_log_takes_precedence_over_test_file_heuristic(db_session, mock_tenant_id, seeded_tenant) -> None:
    """If the LLM DID run shell_exec, use that — even if test files
    are also present. The PR10 path is more accurate (it knows what
    the LLM actually ran)."""
    run = await _seed_run(
        db_session,
        mock_tenant_id,
        files_written=[
            {"path": "add.py", "size": 30, "language": "python"},
            {"path": "tests/test_add.py", "size": 60, "language": "python"},
        ],
        shells=[
            {"command": "python -m pytest tests/test_add.py -v", "exit_code": 0, "duration_ms": 200},
        ],
    )
    deliverable = await publish_run_output(run, db_session)
    await db_session.commit()

    refreshed = (await db_session.execute(select(Deliverable).where(Deliverable.id == deliverable.id))).scalar_one()
    # PR10 path: command from shell, derived_from=local_tool_log.shell_exec.
    assert refreshed.verifier_inputs["command"][2] == "python -m pytest tests/test_add.py -v"
    assert refreshed.verifier_inputs.get("derived_from") == "local_tool_log.shell_exec"


@pytest.mark.asyncio
async def test_handles_top_level_test_file_pattern(db_session, mock_tenant_id, seeded_tenant) -> None:
    """Pytest convention also supports ``test_*.py`` at workspace
    root, not only under ``tests/``. Both patterns should fire."""
    run = await _seed_run(
        db_session,
        mock_tenant_id,
        files_written=[
            {"path": "add.py", "size": 30, "language": "python"},
            {"path": "test_add.py", "size": 60, "language": "python"},
        ],
        shells=[],
    )
    deliverable = await publish_run_output(run, db_session)
    await db_session.commit()

    refreshed = (await db_session.execute(select(Deliverable).where(Deliverable.id == deliverable.id))).scalar_one()
    assert refreshed.verifier_type == "software_test"
    assert "test_add.py" in refreshed.verifier_inputs["command"][2]


@pytest.mark.asyncio
async def test_blocked_loop_with_empty_inline_still_creates_deliverable(
    db_session, mock_tenant_id, seeded_tenant
) -> None:
    """PR11 regression — qwen3-coder dogfood iter 4 case: blocked at
    round-cap with EMPTY chat reply (LLM consumed all tokens on tool
    calls, never emitted prose or fenced block). Old guard bailed on
    ``not summary and not inline and not files`` even though
    ``local_tool_log`` had real work. Backend must still publish the
    deliverable and stamp verification from the test files on disk."""
    run = await _seed_run(
        db_session,
        mock_tenant_id,
        inline="",  # round-cap: no chat content emitted
        files_written=[
            {"path": "add.py", "size": 30, "language": "python"},
            {"path": "tests/test_add.py", "size": 60, "language": "python"},
        ],
        shells=[],
    )
    # Force blocked status — round-cap path transitions to blocked.
    run.status = RunStatus.blocked
    await db_session.commit()

    deliverable = await publish_run_output(run, db_session)
    await db_session.commit()

    assert deliverable is not None
    refreshed = (
        await db_session.execute(select(Deliverable).where(Deliverable.id == deliverable.id))
    ).scalar_one()
    assert refreshed.verifier_type == "software_test"
    assert "tests/test_add.py" in refreshed.verifier_inputs["command"][2]
    assert refreshed.verifier_inputs.get("derived_from") == "workspace_test_files"


@pytest.mark.asyncio
async def test_picks_all_test_files_into_one_pytest_call(db_session, mock_tenant_id, seeded_tenant) -> None:
    """Multiple test files → single pytest call covering all of them
    (one verifier run, not N)."""
    run = await _seed_run(
        db_session,
        mock_tenant_id,
        files_written=[
            {"path": "src/a.py", "size": 30},
            {"path": "tests/test_a.py", "size": 60},
            {"path": "tests/test_b.py", "size": 50},
        ],
        shells=[],
    )
    deliverable = await publish_run_output(run, db_session)
    await db_session.commit()

    refreshed = (await db_session.execute(select(Deliverable).where(Deliverable.id == deliverable.id))).scalar_one()
    cmd_str = refreshed.verifier_inputs["command"][2]
    assert "tests/test_a.py" in cmd_str
    assert "tests/test_b.py" in cmd_str
