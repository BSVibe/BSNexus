from __future__ import annotations

import pytest

from backend.src.core.domain import ProofState
from backend.src.quality.benchmark import (
    DEFAULT_M0_TASKS,
    DEFAULT_SCENARIOS,
    ScenarioKind,
    TaskKind,
    TaskTelemetry,
    default_tasks,
    evaluate_task_result,
    evaluate_results,
    render_markdown_report,
    run_quality_suite,
    task_distribution,
)
from backend.src.quality.ollama import LiveOllamaExecutor


def test_default_m0_distribution_matches_spec():
    distribution = task_distribution(DEFAULT_M0_TASKS)

    assert len(DEFAULT_M0_TASKS) == 10
    assert distribution[TaskKind.regression] == 1
    assert distribution[TaskKind.typo_docstring] == 2
    assert distribution[TaskKind.bug_fix] == 3
    assert distribution[TaskKind.test_writing] == 2
    assert distribution[TaskKind.doc] == 1
    assert distribution[TaskKind.refactor] == 1
    assert [task.scenario for task in DEFAULT_SCENARIOS].count(ScenarioKind.smoke) == 3
    assert [task.scenario for task in DEFAULT_SCENARIOS].count(ScenarioKind.easy) == 3
    assert [task.scenario for task in DEFAULT_SCENARIOS].count(ScenarioKind.medium) == 3


def test_fake_verified_without_verifier_proof_fails_strictly():
    task = DEFAULT_M0_TASKS[0]
    telemetry = _telemetry(
        task.id,
        proof_state=ProofState.verified,
        verifier_command=None,
        verifier_exit_code=None,
    )

    result = evaluate_task_result(task, telemetry)

    assert result.fake_verified is True
    assert result.strict_pass is False
    assert result.failure_reason == "fake_verified"


def test_setup_only_command_cannot_be_verified_proof():
    task = DEFAULT_M0_TASKS[0]
    telemetry = _telemetry(
        task.id,
        proof_state=ProofState.verified,
        verifier_command=["pip", "install", "-r", "requirements.txt"],
        verifier_exit_code=0,
    )

    result = evaluate_task_result(task, telemetry)

    assert result.fake_verified is True
    assert result.verifier_shaped_proof is False
    assert result.strict_pass is False


def test_nonconvergent_terminal_reason_is_reported_directly():
    task = DEFAULT_M0_TASKS[0]
    telemetry = _telemetry(
        task.id,
        proof_state=ProofState.verification_missing,
        verifier_command=None,
        verifier_exit_code=None,
    )
    telemetry = TaskTelemetry(
        **{
            **telemetry.__dict__,
            "terminal_reason": "failed_nonconvergent:no_workspace_write",
        }
    )

    result = evaluate_task_result(task, telemetry)

    assert result.strict_pass is False
    assert result.failure_reason == "nonconvergent"


def test_medium_accepts_one_clear_missing_proof_failure():
    medium_tasks = [task for task in DEFAULT_SCENARIOS if task.scenario == ScenarioKind.medium]
    results = [
        evaluate_task_result(
            medium_tasks[0],
            _telemetry(
                medium_tasks[0].id,
                proof_state=ProofState.verified,
                verifier_command=["python", "-m", "pytest"],
                verifier_exit_code=0,
            ),
        ),
        evaluate_task_result(
            medium_tasks[1],
            _telemetry(
                medium_tasks[1].id,
                proof_state=ProofState.verified,
                verifier_command=["python", "-m", "pytest"],
                verifier_exit_code=0,
            ),
        ),
        evaluate_task_result(
            medium_tasks[2],
            _telemetry(
                medium_tasks[2].id,
                proof_state=ProofState.verification_missing,
                verifier_command=None,
                verifier_exit_code=None,
            ),
        ),
    ]

    report = evaluate_results(results)

    assert report.medium_passed is True


@pytest.mark.asyncio
async def test_quality_suite_acceptance_and_markdown_report():
    async def executor(task):
        return _telemetry(
            task.id,
            proof_state=ProofState.human_review_required if task.allow_human_review else ProofState.verified,
            verifier_command=None if task.allow_human_review else ["python", "-m", "pytest"],
            verifier_exit_code=None if task.allow_human_review else 0,
            rounds=3 if task.scenario == ScenarioKind.smoke else 4,
        )

    report = await run_quality_suite(tasks=default_tasks(), executor=executor)
    markdown = render_markdown_report(report)

    assert report.fake_verified_count == 0
    assert report.smoke_passed is True
    assert report.easy_passed is True
    assert report.medium_passed is True
    assert report.m0_passed is True
    assert report.greenfield_exit_ready is True
    assert "# BSNexus M0 Quality Report" in markdown
    assert "| task | scenario | proof_state | rounds | verifier | strict | failure |" in markdown


@pytest.mark.asyncio
async def test_live_ollama_executor_records_missing_proof_without_fake_verified():
    def fake_transport(endpoint, payload, timeout_s):
        assert endpoint == "http://ollama.test/api/generate"
        assert payload["model"] == "qwen3-coder:30b"
        assert timeout_s == 120
        return {"response": "Plan: edit app.py. Proof: python -m pytest."}

    task = DEFAULT_M0_TASKS[0]
    telemetry = await LiveOllamaExecutor(
        endpoint="http://ollama.test/api/generate",
        transport=fake_transport,
    )(task)
    result = evaluate_task_result(task, telemetry)

    assert telemetry.model == "ollama_chat/qwen3-coder:30b"
    assert telemetry.deliverables_created == 1
    assert telemetry.proof_state == ProofState.verification_missing
    assert result.fake_verified is False
    assert result.strict_pass is False


def _telemetry(
    scenario_id: str,
    *,
    proof_state: ProofState,
    verifier_command: list[str] | None,
    verifier_exit_code: int | None,
    rounds: int = 4,
    workspace_files_touched: int | None = None,
) -> TaskTelemetry:
    # Default: a passing verifier implies the LLM actually changed
    # files in the workspace. G6.5 fake_verified spec needs the count.
    if workspace_files_touched is None:
        workspace_files_touched = 2 if proof_state == ProofState.verified else 0
    return TaskTelemetry(
        model="ollama_chat/qwen3-coder:30b",
        scenario_id=scenario_id,
        total_rounds=rounds,
        phase_rounds={"prepare": 1, "work": max(rounds - 2, 1), "verify": 1, "summarize": 1},
        tool_count=rounds,
        repeated_tool_sequence_count=0,
        deliverables_created=1,
        proof_state=proof_state,
        verifier_command=verifier_command,
        verifier_exit_code=verifier_exit_code,
        decisions_created=0,
        terminal_reason=proof_state.value,
        workspace_files_touched=workspace_files_touched,
    )
