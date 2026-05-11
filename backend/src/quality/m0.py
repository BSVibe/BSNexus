from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
from statistics import median
from typing import Any, Protocol

from backend.src.core.domain import ProofState
from backend.src.core.proof import is_setup_only_command


class ScenarioKind(StrEnum):
    smoke = "smoke"
    easy = "easy"
    medium = "medium"
    m0 = "m0"


class TaskKind(StrEnum):
    regression = "regression"
    typo_docstring = "typo_docstring"
    bug_fix = "bug_fix"
    test_writing = "test_writing"
    doc = "doc"
    refactor = "refactor"


@dataclass(frozen=True)
class BenchmarkTask:
    id: str
    scenario: ScenarioKind
    kind: TaskKind
    title: str
    prompt: str
    expected_proof: str
    allow_human_review: bool = False


@dataclass(frozen=True)
class TaskTelemetry:
    model: str
    scenario_id: str
    total_rounds: int
    phase_rounds: dict[str, int]
    tool_count: int
    repeated_tool_sequence_count: int
    deliverables_created: int
    proof_state: ProofState
    verifier_command: list[str] | None
    verifier_exit_code: int | None
    decisions_created: int
    terminal_reason: str


@dataclass(frozen=True)
class TaskResult:
    task: BenchmarkTask
    telemetry: TaskTelemetry
    strict_pass: bool
    fake_verified: bool
    unnecessary_founder_decision: bool
    round_cap_blocked: bool
    verifier_shaped_proof: bool
    failure_reason: str | None = None


@dataclass(frozen=True)
class AcceptanceReport:
    results: list[TaskResult]
    fake_verified_count: int
    unnecessary_founder_decision_count: int
    round_cap_blocked_count: int
    strict_pass_count: int
    smoke_passed: bool
    easy_passed: bool
    medium_passed: bool
    m0_passed: bool

    @property
    def greenfield_exit_ready(self) -> bool:
        return self.smoke_passed and self.easy_passed and self.medium_passed and self.m0_passed

    def to_dict(self) -> dict[str, Any]:
        return {
            "fake_verified_count": self.fake_verified_count,
            "unnecessary_founder_decision_count": self.unnecessary_founder_decision_count,
            "round_cap_blocked_count": self.round_cap_blocked_count,
            "strict_pass_count": self.strict_pass_count,
            "smoke_passed": self.smoke_passed,
            "easy_passed": self.easy_passed,
            "medium_passed": self.medium_passed,
            "m0_passed": self.m0_passed,
            "greenfield_exit_ready": self.greenfield_exit_ready,
            "results": [
                {
                    "task": asdict(result.task),
                    "telemetry": {
                        **asdict(result.telemetry),
                        "proof_state": result.telemetry.proof_state.value,
                    },
                    "strict_pass": result.strict_pass,
                    "fake_verified": result.fake_verified,
                    "unnecessary_founder_decision": result.unnecessary_founder_decision,
                    "round_cap_blocked": result.round_cap_blocked,
                    "verifier_shaped_proof": result.verifier_shaped_proof,
                    "failure_reason": result.failure_reason,
                }
                for result in self.results
            ],
        }


class QualityExecutor(Protocol):
    async def __call__(self, task: BenchmarkTask) -> TaskTelemetry: ...


async def run_quality_suite(
    *,
    tasks: Sequence[BenchmarkTask],
    executor: QualityExecutor | Callable[[BenchmarkTask], Awaitable[TaskTelemetry]],
) -> AcceptanceReport:
    results = [evaluate_task_result(task, await executor(task)) for task in tasks]
    return evaluate_results(results)


def evaluate_task_result(task: BenchmarkTask, telemetry: TaskTelemetry) -> TaskResult:
    verifier_shaped_proof = _has_verifier_shaped_proof(telemetry)
    fake_verified = telemetry.proof_state == ProofState.verified and not verifier_shaped_proof
    round_cap_blocked = "round_cap" in telemetry.terminal_reason or "round_budget" in telemetry.terminal_reason
    unnecessary_founder_decision = (
        task.scenario in {ScenarioKind.easy, ScenarioKind.m0}
        and telemetry.decisions_created > 0
        and telemetry.proof_state != ProofState.human_review_required
    )
    strict_pass = (
        telemetry.proof_state == ProofState.verified
        and verifier_shaped_proof
        and not fake_verified
        and not round_cap_blocked
        and telemetry.deliverables_created > 0
    )

    failure_reason = None
    if not strict_pass:
        failure_reason = _failure_reason(telemetry, fake_verified, round_cap_blocked)

    return TaskResult(
        task=task,
        telemetry=telemetry,
        strict_pass=strict_pass,
        fake_verified=fake_verified,
        unnecessary_founder_decision=unnecessary_founder_decision,
        round_cap_blocked=round_cap_blocked,
        verifier_shaped_proof=verifier_shaped_proof,
        failure_reason=failure_reason,
    )


def evaluate_results(results: Sequence[TaskResult]) -> AcceptanceReport:
    result_list = list(results)
    fake_verified_count = sum(result.fake_verified for result in result_list)
    unnecessary_decision_count = sum(result.unnecessary_founder_decision for result in result_list)
    round_cap_count = sum(result.round_cap_blocked for result in result_list)
    strict_pass_count = sum(result.strict_pass for result in result_list)

    return AcceptanceReport(
        results=result_list,
        fake_verified_count=fake_verified_count,
        unnecessary_founder_decision_count=unnecessary_decision_count,
        round_cap_blocked_count=round_cap_count,
        strict_pass_count=strict_pass_count,
        smoke_passed=_smoke_passed(result_list),
        easy_passed=_easy_passed(result_list),
        medium_passed=_medium_passed(result_list),
        m0_passed=_m0_passed(result_list),
    )


def render_markdown_report(report: AcceptanceReport) -> str:
    lines = [
        "# BSNexus M0 Quality Report",
        "",
        f"- fake_verified: {report.fake_verified_count}",
        f"- unnecessary_founder_decisions: {report.unnecessary_founder_decision_count}",
        f"- round_cap_blocked: {report.round_cap_blocked_count}",
        f"- strict_pass: {report.strict_pass_count}/{len(report.results)}",
        f"- smoke_passed: {report.smoke_passed}",
        f"- easy_passed: {report.easy_passed}",
        f"- medium_passed: {report.medium_passed}",
        f"- m0_passed: {report.m0_passed}",
        f"- greenfield_exit_ready: {report.greenfield_exit_ready}",
        "",
        "| task | scenario | proof_state | rounds | verifier | strict | failure |",
        "| --- | --- | --- | ---: | --- | --- | --- |",
    ]
    for result in report.results:
        telemetry = result.telemetry
        command = " ".join(telemetry.verifier_command or [])
        lines.append(
            "| "
            f"{result.task.id} | "
            f"{result.task.scenario.value} | "
            f"{telemetry.proof_state.value} | "
            f"{telemetry.total_rounds} | "
            f"{command or '-'} | "
            f"{result.strict_pass} | "
            f"{result.failure_reason or '-'} |"
        )
    return "\n".join(lines) + "\n"


def default_tasks() -> list[BenchmarkTask]:
    return [*DEFAULT_SCENARIOS, *DEFAULT_M0_TASKS]


def _has_verifier_shaped_proof(telemetry: TaskTelemetry) -> bool:
    command = telemetry.verifier_command
    if not command:
        return False
    if telemetry.verifier_exit_code != 0:
        return False
    if is_setup_only_command(command):
        return False
    return any(part in command for part in ("pytest", "test", "build", "curl", "compileall", "tsc"))


def _failure_reason(telemetry: TaskTelemetry, fake_verified: bool, round_cap_blocked: bool) -> str:
    if fake_verified:
        return "fake_verified"
    if round_cap_blocked:
        return "round_cap_blocked"
    if telemetry.proof_state == ProofState.verification_failed:
        return "verification_failed"
    if telemetry.proof_state == ProofState.human_review_required:
        return "human_review_required"
    if telemetry.proof_state == ProofState.verification_missing:
        return "verification_missing"
    return telemetry.terminal_reason or "not_verified"


def _smoke_passed(results: Sequence[TaskResult]) -> bool:
    smoke = [result for result in results if result.task.scenario == ScenarioKind.smoke]
    if not smoke:
        return False
    return (
        len(smoke) == 3
        and all(not result.round_cap_blocked for result in smoke)
        and all(result.telemetry.deliverables_created > 0 for result in smoke)
        and all(
            result.telemetry.proof_state in {ProofState.verified, ProofState.human_review_required} for result in smoke
        )
        and all(not result.fake_verified for result in smoke)
    )


def _easy_passed(results: Sequence[TaskResult]) -> bool:
    easy = [result for result in results if result.task.scenario == ScenarioKind.easy]
    if len(easy) != 3:
        return False
    return all(result.strict_pass for result in easy) and _median_rounds(easy) <= 6


def _medium_passed(results: Sequence[TaskResult]) -> bool:
    medium = [result for result in results if result.task.scenario == ScenarioKind.medium]
    if len(medium) != 3:
        return False
    allowed_failures = {
        "human_review_required",
        "nonconvergent",
        "verification_failed",
        "verification_missing",
    }
    return (
        sum(result.strict_pass for result in medium) >= 2
        and all(not result.fake_verified for result in medium)
        and all(result.failure_reason is None or result.failure_reason in allowed_failures for result in medium)
        and _median_rounds(medium) <= 10
    )


def _m0_passed(results: Sequence[TaskResult]) -> bool:
    m0 = [result for result in results if result.task.scenario == ScenarioKind.m0]
    if len(m0) != 10:
        return False
    return (
        sum(result.strict_pass for result in m0) >= 7
        and all(not result.fake_verified for result in m0)
        and sum(result.unnecessary_founder_decision for result in m0) <= 1
        and sum(result.round_cap_blocked for result in m0) <= 1
        and all(result.verifier_shaped_proof for result in m0 if result.telemetry.proof_state == ProofState.verified)
    )


def _median_rounds(results: Sequence[TaskResult]) -> float:
    return float(median(result.telemetry.total_rounds for result in results))


def task_distribution(tasks: Sequence[BenchmarkTask]) -> Counter[TaskKind]:
    return Counter(task.kind for task in tasks)


def passing_telemetry(
    task: BenchmarkTask, *, rounds: int = 4, model: str = "ollama_chat/qwen3-coder:30b"
) -> TaskTelemetry:
    return TaskTelemetry(
        model=model,
        scenario_id=task.id,
        total_rounds=rounds,
        phase_rounds={"prepare": 1, "work": max(rounds - 2, 1), "verify": 1, "summarize": 1},
        tool_count=rounds,
        repeated_tool_sequence_count=0,
        deliverables_created=1,
        proof_state=ProofState.verified,
        verifier_command=["python", "-m", "pytest"],
        verifier_exit_code=0,
        decisions_created=0,
        terminal_reason="verified",
    )


def human_review_telemetry(task: BenchmarkTask) -> TaskTelemetry:
    return TaskTelemetry(
        model="ollama_chat/qwen3-coder:30b",
        scenario_id=task.id,
        total_rounds=3,
        phase_rounds={"prepare": 1, "work": 1, "verify": 1, "summarize": 0},
        tool_count=3,
        repeated_tool_sequence_count=0,
        deliverables_created=1,
        proof_state=ProofState.human_review_required,
        verifier_command=None,
        verifier_exit_code=None,
        decisions_created=0,
        terminal_reason="human_review_required",
    )


async def run_with_static_executor(tasks: Sequence[BenchmarkTask]) -> AcceptanceReport:
    async def executor(task: BenchmarkTask) -> TaskTelemetry:
        await asyncio.sleep(0)
        if task.allow_human_review:
            return human_review_telemetry(task)
        return passing_telemetry(task)

    return await run_quality_suite(tasks=tasks, executor=executor)


DEFAULT_SCENARIOS: tuple[BenchmarkTask, ...] = (
    BenchmarkTask(
        id="smoke-1",
        scenario=ScenarioKind.smoke,
        kind=TaskKind.doc,
        title="Create tiny validated note",
        prompt="Create a tiny deliverable and mark missing deterministic proof as human review if needed.",
        expected_proof="human_review_or_valid_verifier",
        allow_human_review=True,
    ),
    BenchmarkTask(
        id="smoke-2",
        scenario=ScenarioKind.smoke,
        kind=TaskKind.typo_docstring,
        title="Fix a docstring typo",
        prompt="Fix a docstring typo and produce a proof-aware deliverable.",
        expected_proof="valid_verifier_or_human_review",
        allow_human_review=True,
    ),
    BenchmarkTask(
        id="smoke-3",
        scenario=ScenarioKind.smoke,
        kind=TaskKind.doc,
        title="Summarize proof status",
        prompt="Create a brief proof-status summary with no fake verified state.",
        expected_proof="human_review_or_valid_verifier",
        allow_human_review=True,
    ),
    BenchmarkTask(
        id="easy-1",
        scenario=ScenarioKind.easy,
        kind=TaskKind.test_writing,
        title="Add small function with tests",
        prompt="Add a small pure function and pytest coverage.",
        expected_proof="python -m pytest",
    ),
    BenchmarkTask(
        id="easy-2",
        scenario=ScenarioKind.easy,
        kind=TaskKind.bug_fix,
        title="Fix one-line parser bug",
        prompt="Fix a one-line parser bug and add a regression test.",
        expected_proof="python -m pytest",
    ),
    BenchmarkTask(
        id="easy-3",
        scenario=ScenarioKind.easy,
        kind=TaskKind.typo_docstring,
        title="Docstring plus syntax validation",
        prompt="Fix a docstring and run syntax validation.",
        expected_proof="compile_or_test",
    ),
    BenchmarkTask(
        id="medium-1",
        scenario=ScenarioKind.medium,
        kind=TaskKind.bug_fix,
        title="Small FastAPI route with test",
        prompt="Add a small FastAPI route and an API contract test.",
        expected_proof="python -m pytest",
    ),
    BenchmarkTask(
        id="medium-2",
        scenario=ScenarioKind.medium,
        kind=TaskKind.refactor,
        title="Refactor helper with coverage",
        prompt="Refactor a helper and keep pytest coverage passing.",
        expected_proof="python -m pytest",
    ),
    BenchmarkTask(
        id="medium-3",
        scenario=ScenarioKind.medium,
        kind=TaskKind.test_writing,
        title="Add Node test path",
        prompt="Add a small Node test path and run package test script.",
        expected_proof="npm test or pnpm test",
    ),
)


DEFAULT_M0_TASKS: tuple[BenchmarkTask, ...] = (
    BenchmarkTask(
        id="m0-1",
        scenario=ScenarioKind.m0,
        kind=TaskKind.regression,
        title="Restore /healthz after auth refactor",
        prompt=(
            "Our production smoke test broke after the auth refactor — the /healthz route "
            "returns 401 instead of 200. Restore the public health endpoint so it returns "
            '{"status": "ok"} with HTTP 200 even without an Authorization header, and add a '
            "regression test so we don't lose this again."
        ),
        expected_proof="python -m pytest",
    ),
    BenchmarkTask(
        id="m0-2",
        scenario=ScenarioKind.m0,
        kind=TaskKind.typo_docstring,
        title="Fix 'recieve' → 'receive' across docstrings",
        prompt=(
            'There are several Python docstrings that misspell "receive" as "recieve". '
            "Find every occurrence in src/ and fix them. Don't touch test files."
        ),
        expected_proof="syntax_or_test",
    ),
    BenchmarkTask(
        id="m0-3",
        scenario=ScenarioKind.m0,
        kind=TaskKind.typo_docstring,
        title="README quick-start uses the wrong package manager",
        prompt=(
            "Our README still tells contributors to run `pip install -r requirements.txt`, "
            "but we moved to `uv` months ago. Update the quick-start section and remove the "
            "stale instructions; keep everything else intact."
        ),
        expected_proof="human_review_or_test",
        allow_human_review=True,
    ),
    BenchmarkTask(
        id="m0-4",
        scenario=ScenarioKind.m0,
        kind=TaskKind.bug_fix,
        title="POST returns 500 on missing required fields",
        prompt=(
            "When clients POST a JSON body missing required fields, the API returns 500 with "
            "an opaque stack trace instead of a 422 with a per-field error map. Make the "
            "validation surface 422 and add a test covering the missing-field path."
        ),
        expected_proof="python -m pytest",
    ),
    BenchmarkTask(
        id="m0-5",
        scenario=ScenarioKind.m0,
        kind=TaskKind.bug_fix,
        title="List endpoint leaks across tenants",
        prompt=(
            "The list endpoint returns rows from other tenants when the `tenant_id` filter is "
            "omitted by mistake. Lock it down server-side (never trust the query param alone) "
            "and add a regression test that asserts a cross-tenant request returns an empty list."
        ),
        expected_proof="python -m pytest",
    ),
    BenchmarkTask(
        id="m0-6",
        scenario=ScenarioKind.m0,
        kind=TaskKind.bug_fix,
        title="Signup accepts whitespace-only email",
        prompt=(
            "Our signup form accepts whitespace-only email values because the validator only "
            "checks for empty string. Trim the input and reject if the result is empty; cover "
            "the whitespace case in a unit test."
        ),
        expected_proof="python -m pytest",
    ),
    BenchmarkTask(
        id="m0-7",
        scenario=ScenarioKind.m0,
        kind=TaskKind.test_writing,
        title="Backfill rate-limit middleware coverage",
        prompt=(
            "There's no test for the rate-limit middleware we shipped last sprint. Add unit "
            "tests covering the under-limit, over-limit, and post-window-reset paths so the "
            "next refactor can't silently regress it."
        ),
        expected_proof="python -m pytest",
    ),
    BenchmarkTask(
        id="m0-8",
        scenario=ScenarioKind.m0,
        kind=TaskKind.test_writing,
        title="EncryptionManager round-trip test",
        prompt=(
            "EncryptionManager has no round-trip test — write one that encrypts a value with "
            "a fresh key, decrypts it back, and asserts equality. This is a guard against "
            "future key-rotation work breaking the at-rest contract."
        ),
        expected_proof="python -m pytest",
    ),
    BenchmarkTask(
        id="m0-9",
        scenario=ScenarioKind.m0,
        kind=TaskKind.doc,
        title="Sync the API reference table with current routes",
        prompt=(
            "The API reference table in the README is out of date — `/api/v1/runs` was "
            "renamed and several routes added/removed last quarter. Walk the routers and "
            "update the table to match what actually ships. No code changes."
        ),
        expected_proof="human_review_or_test",
        allow_human_review=True,
    ),
    BenchmarkTask(
        id="m0-10",
        scenario=ScenarioKind.m0,
        kind=TaskKind.refactor,
        title="Extract duplicated session boilerplate",
        prompt=(
            "Three places repeat the same `async with session_factory() as session: ...` "
            "block with an identical fetch-update-commit shape. Extract a small helper, "
            "migrate the three call sites, and make sure the existing tests still pass."
        ),
        expected_proof="python -m pytest",
    ),
)
