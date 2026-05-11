from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass, field
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
    # G6.9 — optional per-task fixture: relative_path → file content.
    # When set, the bridge resets ``workspace_root`` to exactly this
    # tree before the LLM runs, so each task starts from a concrete
    # broken state (failing test + half-written code) instead of
    # forcing the model to invent everything from scratch. Default
    # (None) preserves the bare-workspace behavior G6.7/G6.8
    # measured against.
    seed_workspace: dict[str, str] | None = field(default=None, hash=False, compare=False)


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
    # G6.5 — workspace_files_touched is the number of distinct files the
    # bridge observed change/create/delete in workspace_root between the
    # measurement start and end. Used by ``evaluate_task_result`` to mark
    # a ``verified`` deliverable as ``fake_verified`` when the LLM didn't
    # actually touch the tree (always-passing pytest re-run masquerading
    # as work). Default 0 keeps older callers strict-passable through the
    # existing definition.
    workspace_files_touched: int = 0


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
    # G6.5 — a ``verified`` deliverable is only real work when both
    #   1. the verifier ran a non-setup-only command and exited 0
    #      (``verifier_shaped_proof``), AND
    #   2. the LLM actually changed the workspace
    #      (``workspace_files_touched > 0``)
    # Otherwise the verifier was just re-running a pre-existing passing
    # check against an untouched tree — the canonical "fake verified"
    # pattern surfaced in the 2026-05-11 first live run.
    fake_verified = telemetry.proof_state == ProofState.verified and (
        not verifier_shaped_proof or telemetry.workspace_files_touched <= 0
    )
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
    if "failed_nonconvergent" in telemetry.terminal_reason or "nonconvergent" in telemetry.terminal_reason:
        return "nonconvergent"
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
        # G6.5 — a passing telemetry implies LLM-driven file changes.
        # Static-executor stubs use this; live measurement supplies a
        # real count from the workspace diff.
        workspace_files_touched=2,
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
        title="Add project README",
        prompt=(
            "This workspace has no README. Create ``README.md`` whose first non-empty line "
            "is exactly ``# notes``. The regression test pins that exact heading."
        ),
        expected_proof="python -m pytest",
        seed_workspace={
            "pyproject.toml": (
                "[project]\nname = 'smoke-1-readme'\nversion = '0.0.0'\n"
                "requires-python = '>=3.11'\n"
            ),
            "tests/__init__.py": "",
            "tests/test_readme.py": (
                "import pathlib\n\n\n"
                "def test_readme_has_notes_heading():\n"
                "    root = pathlib.Path(__file__).resolve().parent.parent\n"
                "    readme = root / 'README.md'\n"
                "    assert readme.exists(), 'README.md is missing'\n"
                "    first_line = next(\n"
                "        (line for line in readme.read_text().splitlines() if line.strip()),\n"
                "        '',\n"
                "    )\n"
                "    assert first_line.strip() == '# notes', f'unexpected first line: {first_line!r}'\n"
            ),
        },
    ),
    BenchmarkTask(
        id="smoke-2",
        scenario=ScenarioKind.smoke,
        kind=TaskKind.typo_docstring,
        title="Fix 'Helo' typo in src/greeter.py docstring",
        prompt=(
            "src/greeter.py module docstring starts with 'Helo, world.' — fix it to "
            "'Hello, world.' so the regression test (which reads ``greeter.__doc__``) passes."
        ),
        expected_proof="python -m pytest",
        seed_workspace={
            "pyproject.toml": (
                "[project]\nname = 'smoke-2-greeter'\nversion = '0.0.0'\n"
                "requires-python = '>=3.11'\n"
            ),
            "src/__init__.py": "",
            "src/greeter.py": (
                '"""Helo, world.\n\n'
                "This module is a placeholder for greeting helpers. The opening word\n"
                "above is misspelled.\n"
                '"""\n'
            ),
            "tests/__init__.py": "",
            "tests/test_greeter.py": (
                "from src import greeter\n\n\n"
                "def test_module_docstring_starts_with_hello():\n"
                "    doc = greeter.__doc__ or ''\n"
                "    assert doc.splitlines()[0] == 'Hello, world.', doc.splitlines()[0]\n"
            ),
        },
    ),
    BenchmarkTask(
        id="smoke-3",
        scenario=ScenarioKind.smoke,
        kind=TaskKind.doc,
        title="Add CHANGELOG.md Unreleased section",
        prompt=(
            "Create ``CHANGELOG.md`` whose first non-empty line is exactly "
            "``## [Unreleased]``. The regression test pins that marker."
        ),
        expected_proof="python -m pytest",
        seed_workspace={
            "pyproject.toml": (
                "[project]\nname = 'smoke-3-changelog'\nversion = '0.0.0'\n"
                "requires-python = '>=3.11'\n"
            ),
            "tests/__init__.py": "",
            "tests/test_changelog.py": (
                "import pathlib\n\n\n"
                "def test_changelog_has_unreleased_section():\n"
                "    root = pathlib.Path(__file__).resolve().parent.parent\n"
                "    changelog = root / 'CHANGELOG.md'\n"
                "    assert changelog.exists(), 'CHANGELOG.md is missing'\n"
                "    first_line = next(\n"
                "        (line for line in changelog.read_text().splitlines() if line.strip()),\n"
                "        '',\n"
                "    )\n"
                "    assert first_line.strip() == '## [Unreleased]', first_line\n"
            ),
        },
    ),
    BenchmarkTask(
        id="easy-1",
        scenario=ScenarioKind.easy,
        kind=TaskKind.test_writing,
        title="Implement sum_positive",
        prompt=(
            "src/util.py exposes ``sum_positive(xs)`` as a stub that raises "
            "NotImplementedError. Implement it to return the sum of strictly positive "
            "integers in ``xs`` (skip zero and negatives). Tests pin both an all-positive "
            "list and a mixed list."
        ),
        expected_proof="python -m pytest",
        seed_workspace={
            "pyproject.toml": (
                "[project]\nname = 'easy-1-sum-positive'\nversion = '0.0.0'\n"
                "requires-python = '>=3.11'\n"
            ),
            "src/__init__.py": "",
            "src/util.py": (
                '"""Numeric utilities."""\n\n\n'
                "def sum_positive(xs):\n"
                '    """Return the sum of strictly positive ints in ``xs``."""\n'
                "    raise NotImplementedError\n"
            ),
            "tests/__init__.py": "",
            "tests/test_util.py": (
                "from src.util import sum_positive\n\n\n"
                "def test_sum_positive_all_positive():\n"
                "    assert sum_positive([1, 2, 3]) == 6\n\n\n"
                "def test_sum_positive_mixed():\n"
                "    assert sum_positive([-1, 0, 4, -2, 5]) == 9\n\n\n"
                "def test_sum_positive_empty():\n"
                "    assert sum_positive([]) == 0\n"
            ),
        },
    ),
    BenchmarkTask(
        id="easy-2",
        scenario=ScenarioKind.easy,
        kind=TaskKind.bug_fix,
        title="parse_int drops leading '+' sign",
        prompt=(
            "src/parser.py::parse_int strips whitespace and parses digits, but it rejects "
            "a leading '+' (returns None for '+5'). Fix it to accept an optional leading "
            "'+'. The negative-sign and digit paths must still work. Tests pin '+5', '5', "
            "'-3', and invalid input."
        ),
        expected_proof="python -m pytest",
        seed_workspace={
            "pyproject.toml": (
                "[project]\nname = 'easy-2-parser'\nversion = '0.0.0'\n"
                "requires-python = '>=3.11'\n"
            ),
            "src/__init__.py": "",
            "src/parser.py": (
                '"""Tiny int parser."""\n\n\n'
                "def parse_int(text):\n"
                "    s = text.strip() if isinstance(text, str) else ''\n"
                "    if not s:\n"
                "        return None\n"
                "    # BUG: only '-' is accepted as a sign; '+' falls through and fails.\n"
                "    if s[0] == '-':\n"
                "        body = s[1:]\n"
                "        sign = -1\n"
                "    else:\n"
                "        body = s\n"
                "        sign = 1\n"
                "    if not body.isdigit():\n"
                "        return None\n"
                "    return sign * int(body)\n"
            ),
            "tests/__init__.py": "",
            "tests/test_parser.py": (
                "from src.parser import parse_int\n\n\n"
                "def test_parse_plain_int():\n"
                "    assert parse_int('5') == 5\n\n\n"
                "def test_parse_negative_int():\n"
                "    assert parse_int('-3') == -3\n\n\n"
                "def test_parse_explicit_positive_int():\n"
                "    assert parse_int('+5') == 5\n\n\n"
                "def test_parse_invalid_returns_none():\n"
                "    assert parse_int('abc') is None\n"
            ),
        },
    ),
    BenchmarkTask(
        id="easy-3",
        scenario=ScenarioKind.easy,
        kind=TaskKind.typo_docstring,
        title="Fix 'Calcuator' typo in src/calc.py docstring",
        prompt=(
            "src/calc.py module docstring opens with 'Calcuator helpers.' — fix the typo to "
            "'Calculator helpers.'. The regression test reads ``calc.__doc__``."
        ),
        expected_proof="python -m pytest",
        seed_workspace={
            "pyproject.toml": (
                "[project]\nname = 'easy-3-calc'\nversion = '0.0.0'\n"
                "requires-python = '>=3.11'\n"
            ),
            "src/__init__.py": "",
            "src/calc.py": (
                '"""Calcuator helpers.\n\n'
                "Pure-Python helpers for basic arithmetic.\n"
                '"""\n\n\n'
                "def add(a, b):\n"
                "    return a + b\n"
            ),
            "tests/__init__.py": "",
            "tests/test_calc.py": (
                "from src import calc\n\n\n"
                "def test_module_docstring_says_calculator():\n"
                "    doc = calc.__doc__ or ''\n"
                "    first = doc.splitlines()[0]\n"
                "    assert first == 'Calculator helpers.', first\n"
            ),
        },
    ),
    BenchmarkTask(
        id="medium-1",
        scenario=ScenarioKind.medium,
        kind=TaskKind.bug_fix,
        title="Router returns 200 for unknown paths",
        prompt=(
            "src/router.py::handle returns 200 for the '/healthz' path (correct), but it "
            "also returns 200 for every other path — it should return 404 for anything "
            "except '/healthz'. Fix the dispatcher. Tests pin both the 200 and the 404 "
            "branch."
        ),
        expected_proof="python -m pytest",
        seed_workspace={
            "pyproject.toml": (
                "[project]\nname = 'medium-1-router'\nversion = '0.0.0'\n"
                "requires-python = '>=3.11'\n"
            ),
            "src/__init__.py": "",
            "src/router.py": (
                '"""Tiny path → status dispatcher."""\n\n\n'
                "def handle(path):\n"
                "    if path == '/healthz':\n"
                "        return 200\n"
                "    # BUG: returns 200 for every other path. Should be 404.\n"
                "    return 200\n"
            ),
            "tests/__init__.py": "",
            "tests/test_router.py": (
                "from src.router import handle\n\n\n"
                "def test_healthz_returns_200():\n"
                "    assert handle('/healthz') == 200\n\n\n"
                "def test_unknown_returns_404():\n"
                "    assert handle('/nope') == 404\n\n\n"
                "def test_root_returns_404():\n"
                "    assert handle('/') == 404\n"
            ),
        },
    ),
    BenchmarkTask(
        id="medium-2",
        scenario=ScenarioKind.medium,
        kind=TaskKind.refactor,
        title="Extract duplicated percent-off helper",
        prompt=(
            "src/pricing.py has two functions, ``apply_member_discount(amount, pct)`` and "
            "``apply_promo_discount(amount, pct)``, that compute "
            "``amount - amount * pct / 100`` independently. Extract a private helper "
            "``_apply_percent(amount, pct)`` and call it from both. Tests pin the existing "
            "behavior; do not change return values."
        ),
        expected_proof="python -m pytest",
        seed_workspace={
            "pyproject.toml": (
                "[project]\nname = 'medium-2-pricing'\nversion = '0.0.0'\n"
                "requires-python = '>=3.11'\n"
            ),
            "src/__init__.py": "",
            "src/pricing.py": (
                '"""Discount helpers."""\n\n\n'
                "def apply_member_discount(amount, pct):\n"
                "    return amount - amount * pct / 100\n\n\n"
                "def apply_promo_discount(amount, pct):\n"
                "    return amount - amount * pct / 100\n"
            ),
            "tests/__init__.py": "",
            "tests/test_pricing.py": (
                "import inspect\n\n"
                "from src import pricing\n"
                "from src.pricing import apply_member_discount, apply_promo_discount\n\n\n"
                "def test_member_discount_value():\n"
                "    assert apply_member_discount(100, 10) == 90\n\n\n"
                "def test_promo_discount_value():\n"
                "    assert apply_promo_discount(200, 25) == 150\n\n\n"
                "def test_shared_helper_is_extracted():\n"
                "    # Refactor must introduce a helper that both call sites use.\n"
                "    helpers = [\n"
                "        name for name in dir(pricing)\n"
                "        if name.startswith('_') and name not in {'__doc__', '__name__'}\n"
                "        and callable(getattr(pricing, name))\n"
                "        and not inspect.isclass(getattr(pricing, name))\n"
                "    ]\n"
                "    assert helpers, 'expected a private helper after refactor'\n"
                "    member_src = inspect.getsource(apply_member_discount)\n"
                "    promo_src = inspect.getsource(apply_promo_discount)\n"
                "    assert any(h in member_src for h in helpers), member_src\n"
                "    assert any(h in promo_src for h in helpers), promo_src\n"
            ),
        },
    ),
    BenchmarkTask(
        id="medium-3",
        scenario=ScenarioKind.medium,
        kind=TaskKind.test_writing,
        title="Cover FifoQueue with three tests",
        prompt=(
            "src/queue.py exposes ``FifoQueue`` with ``push`` / ``pop`` / ``__len__``. "
            "``tests/test_queue.py`` is empty. Add three tests: pop on empty raises "
            "IndexError, FIFO order across multiple pushes, and ``len`` reflects pushes "
            "and pops. Do not modify ``src/queue.py``."
        ),
        expected_proof="python -m pytest",
        seed_workspace={
            "pyproject.toml": (
                "[project]\nname = 'medium-3-queue'\nversion = '0.0.0'\n"
                "requires-python = '>=3.11'\n"
            ),
            "src/__init__.py": "",
            "src/queue.py": (
                '"""Minimal FIFO queue."""\n\n\n'
                "class FifoQueue:\n"
                "    def __init__(self):\n"
                "        self._items = []\n\n"
                "    def push(self, item):\n"
                "        self._items.append(item)\n\n"
                "    def pop(self):\n"
                "        if not self._items:\n"
                "            raise IndexError('pop from empty FifoQueue')\n"
                "        return self._items.pop(0)\n\n"
                "    def __len__(self):\n"
                "        return len(self._items)\n"
            ),
            "tests/__init__.py": "",
            "tests/test_queue.py": (
                '"""Pin three behaviours of FifoQueue. Rewrite each placeholder."""\n\n'
                "import pytest\n\n"
                "from src.queue import FifoQueue\n\n\n"
                "def test_pop_empty_raises_index_error():\n"
                "    pytest.fail('TODO: pop on empty FifoQueue should raise IndexError')\n\n\n"
                "def test_push_pop_preserves_fifo_order():\n"
                "    pytest.fail('TODO: pushing a, b, c then popping should yield a, b, c')\n\n\n"
                "def test_len_reflects_push_and_pop():\n"
                "    pytest.fail('TODO: len() should grow on push and shrink on pop')\n"
            ),
        },
    ),
)


DEFAULT_M0_TASKS: tuple[BenchmarkTask, ...] = (
    BenchmarkTask(
        id="m0-1",
        scenario=ScenarioKind.m0,
        kind=TaskKind.regression,
        title="Restore /healthz after auth refactor",
        prompt=(
            "Our production smoke test broke after the auth refactor — the health endpoint "
            "no longer returns the expected status. Make ``healthz()`` in ``src/api.py`` "
            "return ``{'status': 'ok'}`` so the existing regression test passes."
        ),
        expected_proof="python -m pytest",
        seed_workspace={
            "pyproject.toml": (
                "[project]\nname = 'm0-1-healthz'\nversion = '0.0.0'\n"
                "requires-python = '>=3.11'\n"
            ),
            "src/__init__.py": "",
            "src/api.py": (
                '"""Health API.\n\n'
                "The /healthz endpoint was broken during the auth refactor — it now\n"
                "returns the wrong shape. The single regression test in tests/test_healthz.py\n"
                "pins the expected behavior.\n"
                '"""\n\n\n'
                "def healthz() -> dict:\n"
                '    # BUG: should return {"status": "ok"}\n'
                '    return {"status": "broken"}\n'
            ),
            "tests/__init__.py": "",
            "tests/test_healthz.py": (
                "from src.api import healthz\n\n\n"
                "def test_healthz_returns_ok():\n"
                '    assert healthz() == {"status": "ok"}\n'
            ),
        },
    ),
    BenchmarkTask(
        id="m0-2",
        scenario=ScenarioKind.m0,
        kind=TaskKind.typo_docstring,
        title="Fix 'recieve' → 'receive' across docstrings",
        prompt=(
            'Several Python docstrings in src/ misspell "receive" as "recieve". '
            "Find every occurrence in src/*.py and fix them so the regression test passes."
        ),
        expected_proof="python -m pytest",
        seed_workspace={
            "pyproject.toml": "[project]\nname = 'm0-2-typo'\nversion = '0.0.0'\nrequires-python = '>=3.11'\n",
            "src/__init__.py": "",
            "src/sender.py": (
                '"""Sender module — utilities for sending and recieving messages."""\n\n\n'
                "def receive_payload(channel):\n"
                '    """Recieve a payload from a channel."""\n'
                '    return {"channel": channel}\n\n\n'
                "def send_payload(channel, payload):\n"
                '    """Send a payload via a channel; the recieving end is the listener."""\n'
                '    return {"channel": channel, "payload": payload}\n\n\n'
                "def acknowledge():\n"
                '    """Acknowledge a recieved payload."""\n'
                "    return True\n"
            ),
            "tests/__init__.py": "",
            "tests/test_no_typos.py": (
                "import pathlib\nimport re\n\n"
                "TYPO = re.compile(r'\\brecieve', re.IGNORECASE)\n\n\n"
                "def test_no_recieve_typos_in_src():\n"
                "    src_dir = pathlib.Path(__file__).resolve().parent.parent / 'src'\n"
                "    offenders = []\n"
                "    for py in src_dir.rglob('*.py'):\n"
                "        text = py.read_text()\n"
                "        if TYPO.search(text):\n"
                "            offenders.append(str(py.relative_to(src_dir)))\n"
                "    assert offenders == [], f'recieve typos remain in: {offenders}'\n"
            ),
        },
    ),
    BenchmarkTask(
        id="m0-3",
        scenario=ScenarioKind.m0,
        kind=TaskKind.typo_docstring,
        title="README quick-start still says pip install -r requirements.txt",
        prompt=(
            "``README.md`` Quick Start tells contributors to run "
            "``pip install -r requirements.txt`` but we moved to ``uv`` months ago. "
            "Replace that line with ``uv sync`` (keep the rest of the README unchanged). "
            "The regression test asserts the README now contains ``uv sync`` and no longer "
            "mentions ``pip install -r requirements.txt``."
        ),
        expected_proof="python -m pytest",
        seed_workspace={
            "pyproject.toml": (
                "[project]\nname = 'm0-3-readme'\nversion = '0.0.0'\n"
                "requires-python = '>=3.11'\n"
            ),
            "README.md": (
                "# m0-3-readme\n\n"
                "## Quick Start\n\n"
                "Install dependencies:\n\n"
                "```\npip install -r requirements.txt\n```\n\n"
                "Then run the app.\n"
            ),
            "tests/__init__.py": "",
            "tests/test_readme.py": (
                "import pathlib\n\n\n"
                "def test_readme_uses_uv_sync():\n"
                "    root = pathlib.Path(__file__).resolve().parent.parent\n"
                "    text = (root / 'README.md').read_text()\n"
                "    assert 'uv sync' in text, 'README.md should mention `uv sync`'\n"
                "    assert 'pip install -r requirements.txt' not in text, (\n"
                "        'README.md still references the stale pip install line'\n"
                "    )\n"
            ),
        },
    ),
    BenchmarkTask(
        id="m0-4",
        scenario=ScenarioKind.m0,
        kind=TaskKind.bug_fix,
        title="Validation should report missing fields, not crash",
        prompt=(
            "src/validator.py raises a generic KeyError when a required field is missing — "
            "the API surfaces that as a 500. Make ``validate`` raise ``ValidationError`` with "
            "``.fields`` listing every missing key. The regression test pins the contract."
        ),
        expected_proof="python -m pytest",
        seed_workspace={
            "pyproject.toml": "[project]\nname = 'm0-4-validator'\nversion = '0.0.0'\nrequires-python = '>=3.11'\n",
            "src/__init__.py": "",
            "src/validator.py": (
                '"""Payload validation."""\n\n\n'
                "class ValidationError(Exception):\n"
                '    """Raised with .fields = list of missing field names."""\n\n'
                "    def __init__(self, fields):\n"
                '        super().__init__(f"missing fields: {fields}")\n'
                "        self.fields = fields\n\n\n"
                "def validate(payload, required):\n"
                "    # BUG: raises bare KeyError instead of ValidationError(missing).\n"
                "    for field in required:\n"
                "        payload[field]\n"
            ),
            "tests/__init__.py": "",
            "tests/test_validator.py": (
                "import pytest\n\n"
                "from src.validator import ValidationError, validate\n\n\n"
                "def test_missing_fields_raises_validation_error_with_field_list():\n"
                "    with pytest.raises(ValidationError) as exc_info:\n"
                "        validate({'a': 1}, ['a', 'b', 'c'])\n"
                "    assert sorted(exc_info.value.fields) == ['b', 'c']\n\n\n"
                "def test_all_fields_present_passes():\n"
                "    validate({'a': 1, 'b': 2}, ['a', 'b'])\n"
            ),
        },
    ),
    BenchmarkTask(
        id="m0-5",
        scenario=ScenarioKind.m0,
        kind=TaskKind.bug_fix,
        title="List endpoint leaks across tenants when filter omitted",
        prompt=(
            "src/repo.py::list_items currently returns every row when ``tenant_id`` is "
            "omitted. That leaks data across tenants. Make the omitted-tenant case return "
            "an empty list. The regression test pins both behaviours."
        ),
        expected_proof="python -m pytest",
        seed_workspace={
            "pyproject.toml": "[project]\nname = 'm0-5-tenant-leak'\nversion = '0.0.0'\nrequires-python = '>=3.11'\n",
            "src/__init__.py": "",
            "src/repo.py": (
                '"""Tenant-scoped list endpoint."""\n\n'
                "_ITEMS = [\n"
                "    {'id': 1, 'tenant_id': 'a', 'name': 'alpha'},\n"
                "    {'id': 2, 'tenant_id': 'b', 'name': 'beta'},\n"
                "    {'id': 3, 'tenant_id': 'a', 'name': 'gamma'},\n"
                "]\n\n\n"
                "def list_items(tenant_id=None):\n"
                "    # BUG: returns the full list when tenant_id is None.\n"
                "    if tenant_id is None:\n"
                "        return list(_ITEMS)\n"
                "    return [item for item in _ITEMS if item['tenant_id'] == tenant_id]\n"
            ),
            "tests/__init__.py": "",
            "tests/test_repo.py": (
                "from src.repo import list_items\n\n\n"
                "def test_omitted_tenant_returns_empty():\n"
                "    assert list_items() == []\n"
                "    assert list_items(None) == []\n\n\n"
                "def test_explicit_tenant_filters():\n"
                "    names = {item['name'] for item in list_items('a')}\n"
                "    assert names == {'alpha', 'gamma'}\n"
            ),
        },
    ),
    BenchmarkTask(
        id="m0-6",
        scenario=ScenarioKind.m0,
        kind=TaskKind.bug_fix,
        title="Signup accepts whitespace-only email",
        prompt=(
            "src/signup.py::is_valid_email returns True for whitespace-only inputs because "
            "the check happens before trimming. Trim first, then validate. The regression "
            "test pins both the whitespace-only rejection and the trim-then-accept paths."
        ),
        expected_proof="python -m pytest",
        seed_workspace={
            "pyproject.toml": "[project]\nname = 'm0-6-email'\nversion = '0.0.0'\nrequires-python = '>=3.11'\n",
            "src/__init__.py": "",
            "src/signup.py": (
                '"""Email validation for signup."""\n\n\n'
                "def is_valid_email(email):\n"
                "    # BUG: the empty check happens before trimming AND the validation\n"
                "    # forgot to require '@', so whitespace-only and at-less inputs\n"
                "    # both sneak through.\n"
                "    if not email:\n"
                "        return False\n"
                "    return True\n"
            ),
            "tests/__init__.py": "",
            "tests/test_signup.py": (
                "from src.signup import is_valid_email\n\n\n"
                "def test_empty_string_rejected():\n"
                "    assert is_valid_email('') is False\n\n\n"
                "def test_whitespace_only_rejected():\n"
                "    assert is_valid_email('   ') is False\n"
                "    assert is_valid_email('\\t\\n') is False\n\n\n"
                "def test_valid_email_trimmed_accepted():\n"
                "    assert is_valid_email('  user@example.com  ') is True\n\n\n"
                "def test_no_at_sign_rejected():\n"
                "    assert is_valid_email('notanemail') is False\n"
            ),
        },
    ),
    BenchmarkTask(
        id="m0-7",
        scenario=ScenarioKind.m0,
        kind=TaskKind.test_writing,
        title="Backfill rate-limit middleware coverage",
        prompt=(
            "src/rate_limit.py ships a working ``RateLimiter`` but has no real test "
            "coverage. Rewrite the three placeholder tests in tests/test_rate_limit.py "
            "so they actually exercise the under-limit, over-limit, and "
            "after-window-reset paths. Hint: ``monkeypatch.setattr('time.time', ...)`` "
            "to control the clock without sleeping."
        ),
        expected_proof="python -m pytest",
        seed_workspace={
            "pyproject.toml": "[project]\nname = 'm0-7-rate-limit'\nversion = '0.0.0'\nrequires-python = '>=3.11'\n",
            "src/__init__.py": "",
            "src/rate_limit.py": (
                '"""Token-bucket rate limiter (working implementation, no test coverage yet)."""\n\n'
                "import time\n\n\n"
                "class RateLimiter:\n"
                "    def __init__(self, window_s, max_requests):\n"
                "        self.window_s = window_s\n"
                "        self.max_requests = max_requests\n"
                "        self._calls = {}\n\n"
                "    def allow(self, key):\n"
                "        now = time.time()\n"
                "        history = self._calls.setdefault(key, [])\n"
                "        cutoff = now - self.window_s\n"
                "        history[:] = [ts for ts in history if ts >= cutoff]\n"
                "        if len(history) >= self.max_requests:\n"
                "            return False\n"
                "        history.append(now)\n"
                "        return True\n"
            ),
            "tests/__init__.py": "",
            "tests/test_rate_limit.py": (
                '"""Pin three behaviours of the rate limiter. Rewrite each placeholder."""\n\n'
                "import pytest\n\n"
                "from src.rate_limit import RateLimiter\n\n\n"
                "def test_under_limit_allows(monkeypatch):\n"
                "    pytest.fail('TODO: RateLimiter(1.0, 3) should allow 3 calls in the window')\n\n\n"
                "def test_over_limit_blocks(monkeypatch):\n"
                "    pytest.fail('TODO: 4th call within the window should return False')\n\n\n"
                "def test_resets_after_window(monkeypatch):\n"
                "    pytest.fail('TODO: after window elapses, allow() should return True again')\n"
            ),
        },
    ),
    BenchmarkTask(
        id="m0-8",
        scenario=ScenarioKind.m0,
        kind=TaskKind.test_writing,
        title="EncryptionManager round-trip test",
        prompt=(
            "src/crypto.py::EncryptionManager has encrypt + decrypt methods but no test. "
            "Replace the placeholder in tests/test_crypto.py with a real round-trip: encrypt "
            "a plaintext with a fresh key, decrypt the result, assert the original returns."
        ),
        expected_proof="python -m pytest",
        seed_workspace={
            "pyproject.toml": "[project]\nname = 'm0-8-crypto'\nversion = '0.0.0'\nrequires-python = '>=3.11'\n",
            "src/__init__.py": "",
            "src/crypto.py": (
                '"""Toy symmetric encryption — just enough surface to test the round-trip."""\n\n\n'
                "class EncryptionManager:\n"
                "    def __init__(self, key):\n"
                "        self.key = key\n\n"
                "    def encrypt(self, plaintext):\n"
                "        return bytes(b ^ self.key for b in plaintext.encode('utf-8'))\n\n"
                "    def decrypt(self, ciphertext):\n"
                "        return bytes(b ^ self.key for b in ciphertext).decode('utf-8')\n"
            ),
            "tests/__init__.py": "",
            "tests/test_crypto.py": (
                '"""TODO: add a round-trip test."""\n\n'
                "import pytest\n\n"
                "from src.crypto import EncryptionManager\n\n\n"
                "def test_encrypt_decrypt_round_trip_returns_original():\n"
                "    pytest.fail('TODO: encrypt a plaintext, decrypt it, assert it round-trips')\n"
            ),
        },
    ),
    BenchmarkTask(
        id="m0-9",
        scenario=ScenarioKind.m0,
        kind=TaskKind.doc,
        title="API reference table still lists the old /api/v1/runs route",
        prompt=(
            "``README.md`` has an API reference table that still lists ``/api/v1/runs`` — "
            "that endpoint was renamed to ``/api/v1/run-attempts`` last quarter. Update the "
            "table row so the path column reads ``/api/v1/run-attempts`` (keep the rest of "
            "the table and README unchanged). The regression test asserts the new path is "
            "present and the old path is gone."
        ),
        expected_proof="python -m pytest",
        seed_workspace={
            "pyproject.toml": (
                "[project]\nname = 'm0-9-api-ref'\nversion = '0.0.0'\n"
                "requires-python = '>=3.11'\n"
            ),
            "README.md": (
                "# m0-9-api-ref\n\n"
                "## API Reference\n\n"
                "| Path | Method | Description |\n"
                "| ---- | ------ | ----------- |\n"
                "| /api/v1/healthz | GET | Liveness probe |\n"
                "| /api/v1/runs | POST | Start a run (deprecated path) |\n"
                "| /api/v1/deliverables | GET | List deliverables |\n"
            ),
            "tests/__init__.py": "",
            "tests/test_api_reference.py": (
                "import pathlib\n\n\n"
                "def test_api_reference_uses_run_attempts():\n"
                "    root = pathlib.Path(__file__).resolve().parent.parent\n"
                "    text = (root / 'README.md').read_text()\n"
                "    assert '/api/v1/run-attempts' in text, (\n"
                "        'README.md API table should reference /api/v1/run-attempts'\n"
                "    )\n"
                "    assert '/api/v1/runs' not in text, (\n"
                "        'README.md still references the old /api/v1/runs path'\n"
                "    )\n"
            ),
        },
    ),
    BenchmarkTask(
        id="m0-10",
        scenario=ScenarioKind.m0,
        kind=TaskKind.refactor,
        title="Extract duplicated mutation helper",
        prompt=(
            "src/helper.py exposes ``with_row`` but it isn't implemented yet. Implement it "
            "so that ``with_row(rows, row_id, mutate)`` fetches the row, applies "
            "``mutate(row)``, and returns the mutated row. The test pins the contract — "
            "both the return value and that ``rows[row_id]`` reflects the mutation."
        ),
        expected_proof="python -m pytest",
        seed_workspace={
            "pyproject.toml": "[project]\nname = 'm0-10-helper'\nversion = '0.0.0'\nrequires-python = '>=3.11'\n",
            "src/__init__.py": "",
            "src/helper.py": (
                '"""Mutation helper extracted from three near-identical inline blocks."""\n\n\n'
                "def with_row(rows, row_id, mutate):\n"
                "    # TODO(m0-10): fetch rows[row_id], apply mutate(row), return mutated row.\n"
                "    raise NotImplementedError('with_row is not extracted yet')\n"
            ),
            "tests/__init__.py": "",
            "tests/test_helper.py": (
                "from src.helper import with_row\n\n\n"
                "def test_with_row_applies_mutation_and_returns_row():\n"
                "    rows = {1: {'name': 'alpha', 'count': 0}}\n\n"
                "    def bump(row):\n"
                "        row['count'] += 1\n"
                "        return row\n\n"
                "    result = with_row(rows, 1, bump)\n"
                "    assert result['count'] == 1\n"
                "    assert rows[1]['count'] == 1\n"
            ),
        },
    ),
)


# G6.7 — multi-run aggregation.
#
# 2026-05-11 measurement series exposed a third axis of measurement
# error: same prompt, same workspace, same model → different
# strict_pass tasks across runs. The single-run M0 gate would have
# declared a hand-picked run as authoritative. Wrong.
#
# Spec evolution: M0 acceptance is now defined over K runs.
# A task is "stably passing" iff it strict_pass'd in at least
# ``min_per_task_strict_rate`` (default 0.7) of the K runs. The gate
# requires:
#   - ≥ 7 of the 10 M0 tasks are stably passing, AND
#   - 0 fake_verified events in any of the K runs.
# Single-run measurement is a special case (K=1, identical to the
# pre-G6.7 spec).

DEFAULT_AGGREGATE_RUNS = 3
DEFAULT_MIN_PER_TASK_STRICT_RATE = 0.7


@dataclass(frozen=True)
class TaskRunRate:
    """Per-task aggregation across K runs of the same suite."""

    task_id: str
    scenario: ScenarioKind
    runs_total: int
    strict_pass_runs: int
    fake_verified_runs: int
    verification_failed_runs: int
    round_cap_blocked_runs: int

    @property
    def strict_pass_rate(self) -> float:
        return self.strict_pass_runs / self.runs_total if self.runs_total else 0.0


@dataclass(frozen=True)
class MultiRunReport:
    """Aggregation across K AcceptanceReports for the same suite.

    ``reports`` are kept verbatim so the JSON archive carries every
    per-run telemetry row — the aggregator only adds derived fields.
    """

    reports: list[AcceptanceReport]
    task_rates: dict[str, TaskRunRate] = field(default_factory=dict)
    min_per_task_strict_rate: float = DEFAULT_MIN_PER_TASK_STRICT_RATE

    @property
    def runs_total(self) -> int:
        return len(self.reports)

    @property
    def total_strict_pass_cells(self) -> int:
        return sum(rate.strict_pass_runs for rate in self.task_rates.values())

    @property
    def total_cells(self) -> int:
        return sum(rate.runs_total for rate in self.task_rates.values())

    @property
    def total_fake_verified_cells(self) -> int:
        return sum(rate.fake_verified_runs for rate in self.task_rates.values())

    @property
    def m0_passed(self) -> bool:
        m0_rates = [r for r in self.task_rates.values() if r.scenario == ScenarioKind.m0]
        if len(m0_rates) != 10:
            return False
        any_fake = any(r.fake_verified_runs > 0 for r in m0_rates)
        stably_passing = sum(1 for r in m0_rates if r.strict_pass_rate >= self.min_per_task_strict_rate)
        return not any_fake and stably_passing >= 7

    @property
    def greenfield_exit_ready(self) -> bool:
        return self.m0_passed

    def to_dict(self) -> dict[str, Any]:
        return {
            "runs_total": self.runs_total,
            "total_strict_pass_cells": self.total_strict_pass_cells,
            "total_cells": self.total_cells,
            "total_fake_verified_cells": self.total_fake_verified_cells,
            "min_per_task_strict_rate": self.min_per_task_strict_rate,
            "m0_passed": self.m0_passed,
            "greenfield_exit_ready": self.greenfield_exit_ready,
            "task_rates": {
                task_id: {
                    "task_id": rate.task_id,
                    "scenario": rate.scenario.value,
                    "runs_total": rate.runs_total,
                    "strict_pass_runs": rate.strict_pass_runs,
                    "fake_verified_runs": rate.fake_verified_runs,
                    "verification_failed_runs": rate.verification_failed_runs,
                    "round_cap_blocked_runs": rate.round_cap_blocked_runs,
                    "strict_pass_rate": rate.strict_pass_rate,
                }
                for task_id, rate in self.task_rates.items()
            },
            "per_run_summaries": [
                {
                    "strict_pass_count": report.strict_pass_count,
                    "fake_verified_count": report.fake_verified_count,
                    "round_cap_blocked_count": report.round_cap_blocked_count,
                    "smoke_passed": report.smoke_passed,
                    "easy_passed": report.easy_passed,
                    "medium_passed": report.medium_passed,
                    "m0_passed": report.m0_passed,
                }
                for report in self.reports
            ],
            "runs": [report.to_dict() for report in self.reports],
        }


def aggregate_runs(
    reports: Sequence[AcceptanceReport],
    *,
    min_per_task_strict_rate: float = DEFAULT_MIN_PER_TASK_STRICT_RATE,
) -> MultiRunReport:
    """Roll K AcceptanceReports into per-task rate cells + a gate
    verdict. Tasks present in any report are tracked; scenarios are
    inferred from the first sighting (the suite is expected to be
    identical across runs)."""
    runs_total = len(reports)
    if runs_total == 0:
        return MultiRunReport(
            reports=[], task_rates={}, min_per_task_strict_rate=min_per_task_strict_rate
        )

    strict_counts: dict[str, int] = defaultdict(int)
    fake_counts: dict[str, int] = defaultdict(int)
    failed_counts: dict[str, int] = defaultdict(int)
    capped_counts: dict[str, int] = defaultdict(int)
    scenarios: dict[str, ScenarioKind] = {}

    for report in reports:
        for result in report.results:
            task_id = result.task.id
            scenarios.setdefault(task_id, result.task.scenario)
            if result.strict_pass:
                strict_counts[task_id] += 1
            if result.fake_verified:
                fake_counts[task_id] += 1
            if result.failure_reason == "verification_failed":
                failed_counts[task_id] += 1
            if result.round_cap_blocked:
                capped_counts[task_id] += 1

    task_rates = {
        task_id: TaskRunRate(
            task_id=task_id,
            scenario=scenarios[task_id],
            runs_total=runs_total,
            strict_pass_runs=strict_counts[task_id],
            fake_verified_runs=fake_counts[task_id],
            verification_failed_runs=failed_counts[task_id],
            round_cap_blocked_runs=capped_counts[task_id],
        )
        for task_id in scenarios
    }
    return MultiRunReport(
        reports=list(reports),
        task_rates=task_rates,
        min_per_task_strict_rate=min_per_task_strict_rate,
    )


def render_multi_run_markdown(report: MultiRunReport) -> str:
    """Render a markdown summary of a multi-run aggregate."""
    lines = [
        "# BSNexus M0 Quality Report (multi-run)",
        "",
        f"- runs: {report.runs_total}",
        f"- strict_pass cells: {report.total_strict_pass_cells}/{report.total_cells}",
        f"- fake_verified cells: {report.total_fake_verified_cells}",
        f"- min_per_task_strict_rate threshold: {report.min_per_task_strict_rate:.2f}",
        f"- m0_passed: {report.m0_passed}",
        f"- greenfield_exit_ready: {report.greenfield_exit_ready}",
        "",
        "| task | scenario | strict_pass_rate | fake_runs | failed_runs | capped_runs |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    ordered = sorted(report.task_rates.values(), key=lambda r: (r.scenario.value, r.task_id))
    for rate in ordered:
        lines.append(
            f"| {rate.task_id} | {rate.scenario.value} | "
            f"{rate.strict_pass_runs}/{rate.runs_total} "
            f"({rate.strict_pass_rate:.2f}) | "
            f"{rate.fake_verified_runs} | "
            f"{rate.verification_failed_runs} | "
            f"{rate.round_cap_blocked_runs} |"
        )
    return "\n".join(lines) + "\n"
