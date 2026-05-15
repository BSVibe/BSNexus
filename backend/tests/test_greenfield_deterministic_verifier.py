"""Multi-aspect verifier tests.

Covers:
  - ``select_verification_aspects`` activation rules per aspect_type
  - ``run_verification`` end-to-end against a real tmp workspace
  - ``rollup_proof_state`` semantics across aspect status combinations
  - ``is_setup_only_command`` (M0 helper still exposed)
"""

from __future__ import annotations

import sys
import uuid

import pytest
from sqlalchemy import select

from backend.src.core.domain import (
    DeliverableStatus,
    DeliverableType,
    ProofAspectStatus,
    ProofAspectType,
    ProofState,
    RequestStatus,
)
from backend.src.core.verification import (
    is_setup_only_command,
    rollup_proof_state,
    run_verification,
    select_verification_aspects,
)
from backend.src.core.work_steps import GreenfieldStateError, transition_request
from backend.src.models import Deliverable, Project, Request, VerificationAspect


async def _make_deliverable(
    db_session,
    tenant_id: uuid.UUID,
    *,
    deliverable_type: DeliverableType = DeliverableType.code,
) -> tuple[Request, Deliverable]:
    project = Project(tenant_id=tenant_id, name="Verifier", description="")
    db_session.add(project)
    await db_session.flush()
    request = Request(tenant_id=tenant_id, project_id=project.id, intent="Add /healthz")
    db_session.add(request)
    await db_session.flush()
    deliverable = Deliverable(
        tenant_id=tenant_id,
        project_id=project.id,
        request_id=request.id,
        type=deliverable_type,
        title="Implementation",
        artifact_refs=[{"path": "src/app.py"}],
        status=DeliverableStatus.draft,
        proof_state=ProofState.verification_missing,
    )
    db_session.add(deliverable)
    await db_session.commit()
    await db_session.refresh(deliverable)
    await db_session.refresh(request)
    return request, deliverable


# ────────────────────────────── selection ──────────────────────────────


def test_selects_python_code_test_aspect_from_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n")
    (tmp_path / "tests").mkdir()

    specs = select_verification_aspects(
        workspace_root=tmp_path,
        deliverable_type=DeliverableType.code,
        changed_files=["src/app.py"],
    )

    types = [s.aspect_type for s in specs]
    assert ProofAspectType.code_test in types
    test_spec = next(s for s in specs if s.aspect_type == ProofAspectType.code_test)
    assert test_spec.commands == ((sys.executable, "-m", "pytest"),)


def test_selects_python_code_test_from_root_level_test_file(tmp_path):
    """Bare workspace with a root-level ``test_*.py`` still activates
    code_test — pytest discovers root-level files fine."""
    (tmp_path / "calculator.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "test_calculator.py").write_text(
        "from calculator import add\ndef test_add():\n    assert add(1, 2) == 3\n"
    )

    specs = select_verification_aspects(
        workspace_root=tmp_path,
        deliverable_type=DeliverableType.code,
        changed_files=["calculator.py", "test_calculator.py"],
    )
    types = [s.aspect_type for s in specs]
    assert ProofAspectType.code_test in types


def test_selects_node_code_test_from_package_json(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"test": "vitest run"}}')
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")

    specs = select_verification_aspects(
        workspace_root=tmp_path,
        deliverable_type=DeliverableType.code,
        changed_files=["src/App.tsx"],
    )
    test_spec = next(s for s in specs if s.aspect_type == ProofAspectType.code_test)
    assert test_spec.commands == (("pnpm", "test"),)
    assert "package.json" in test_spec.required_refs


def test_selects_node_build_when_test_script_default(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"build": "vite build"}}')

    specs = select_verification_aspects(
        workspace_root=tmp_path,
        deliverable_type=DeliverableType.preview,
        changed_files=["package.json"],
    )
    test_spec = next(s for s in specs if s.aspect_type == ProofAspectType.code_test)
    assert test_spec.commands == (("npm", "run", "build"),)


def test_no_aspects_for_docs_only_deliverable(tmp_path):
    (tmp_path / "README.md").write_text("# docs only\n")

    specs = select_verification_aspects(
        workspace_root=tmp_path,
        deliverable_type=DeliverableType.doc,
        changed_files=["README.md"],
    )
    assert specs == []


def test_no_aspects_for_lone_python_file_without_test(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n")

    specs = select_verification_aspects(
        workspace_root=tmp_path,
        deliverable_type=DeliverableType.code,
        changed_files=["calc.py"],
    )
    # No pyproject, no tests dir, no test file → no aspects.
    assert specs == []


def test_lint_aspect_activates_when_ruff_declared(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\n"
        "[project.optional-dependencies]\ndev = ['ruff>=0.5.0']\n"
    )
    (tmp_path / "tests").mkdir()

    specs = select_verification_aspects(
        workspace_root=tmp_path,
        deliverable_type=DeliverableType.code,
        changed_files=["app.py"],
    )
    types = [s.aspect_type for s in specs]
    assert ProofAspectType.code_lint in types
    lint = next(s for s in specs if s.aspect_type == ProofAspectType.code_lint)
    assert lint.commands[0][:3] == (sys.executable, "-m", "ruff")


def test_lint_aspect_skipped_when_ruff_not_declared(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\ndependencies=['fastapi']\n")
    (tmp_path / "tests").mkdir()

    specs = select_verification_aspects(
        workspace_root=tmp_path,
        deliverable_type=DeliverableType.code,
        changed_files=["app.py"],
    )
    types = [s.aspect_type for s in specs]
    assert ProofAspectType.code_lint not in types


def test_install_smoke_aspect_activates_with_pyproject_deps(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\ndependencies=['fastapi>=0.110.0']\n"
    )
    specs = select_verification_aspects(
        workspace_root=tmp_path,
        deliverable_type=DeliverableType.code,
        changed_files=["app.py"],
    )
    types = [s.aspect_type for s in specs]
    assert ProofAspectType.code_install_smoke in types


def test_install_smoke_aspect_absent_when_no_deps_declared(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    specs = select_verification_aspects(
        workspace_root=tmp_path,
        deliverable_type=DeliverableType.code,
        changed_files=["app.py"],
    )
    types = [s.aspect_type for s in specs]
    assert ProofAspectType.code_install_smoke not in types


def test_code_build_aspect_activates_when_dockerfile_present(tmp_path):
    """Top-level Dockerfile → code_build aspect added."""
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")
    specs = select_verification_aspects(
        workspace_root=tmp_path,
        deliverable_type=DeliverableType.code,
        changed_files=["Dockerfile"],
    )
    types = [s.aspect_type for s in specs]
    assert ProofAspectType.code_build in types


def test_code_build_aspect_absent_without_dockerfile(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\ndependencies=['fastapi']\n")
    specs = select_verification_aspects(
        workspace_root=tmp_path,
        deliverable_type=DeliverableType.code,
        changed_files=["app.py"],
    )
    types = [s.aspect_type for s in specs]
    assert ProofAspectType.code_build not in types


# ─────────────────────────── setup-only helper ───────────────────────────


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
def test_setup_only_commands_helper(command):
    assert is_setup_only_command(command)


# ─────────────────────────── roll-up semantics ───────────────────────────


class _FakeAspect:
    """In-memory stand-in so roll-up logic can be unit-tested without
    flushing rows to the test DB."""

    def __init__(self, status: ProofAspectStatus, blocking: bool = True) -> None:
        self.status = status
        self.blocking = blocking


def test_rollup_no_aspects_falls_to_human_review():
    assert rollup_proof_state([]) == ProofState.human_review_required


def test_rollup_all_passed_blocking_verifies():
    aspects = [_FakeAspect(ProofAspectStatus.passed), _FakeAspect(ProofAspectStatus.passed)]
    assert rollup_proof_state(aspects) == ProofState.verified  # type: ignore[arg-type]


def test_rollup_any_failed_fails():
    aspects = [_FakeAspect(ProofAspectStatus.passed), _FakeAspect(ProofAspectStatus.failed)]
    assert rollup_proof_state(aspects) == ProofState.verification_failed  # type: ignore[arg-type]


def test_rollup_any_error_falls_to_human_review():
    """An infra error in one aspect shouldn't count as a model failure."""
    aspects = [_FakeAspect(ProofAspectStatus.passed), _FakeAspect(ProofAspectStatus.error)]
    assert rollup_proof_state(aspects) == ProofState.human_review_required  # type: ignore[arg-type]


def test_rollup_still_running_marks_verifying():
    aspects = [_FakeAspect(ProofAspectStatus.passed), _FakeAspect(ProofAspectStatus.running)]
    assert rollup_proof_state(aspects) == ProofState.verifying  # type: ignore[arg-type]


def test_rollup_non_blocking_failures_do_not_block_verified():
    aspects = [
        _FakeAspect(ProofAspectStatus.passed),
        _FakeAspect(ProofAspectStatus.failed, blocking=False),
    ]
    assert rollup_proof_state(aspects) == ProofState.verified  # type: ignore[arg-type]


# ─────────────────────────── end-to-end runs ───────────────────────────


@pytest.mark.asyncio
async def test_run_verification_with_passing_pytest_marks_verified(
    tmp_path, db_session, mock_tenant_id, seeded_tenant
):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    (tmp_path / "test_demo.py").write_text("def test_truth():\n    assert True\n")
    _, deliverable = await _make_deliverable(db_session, mock_tenant_id)

    await run_verification(
        deliverable=deliverable,
        workspace_root=tmp_path,
        changed_files=["test_demo.py"],
        session=db_session,
    )

    await db_session.refresh(deliverable)
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
    test_aspect = next(a for a in aspects if a.aspect_type == ProofAspectType.code_test)
    assert test_aspect.status == ProofAspectStatus.passed
    assert test_aspect.exit_code == 0


@pytest.mark.asyncio
async def test_run_verification_with_failing_pytest_marks_failed_and_blocks_ship(
    tmp_path, db_session, mock_tenant_id, seeded_tenant
):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    (tmp_path / "test_demo.py").write_text("def test_no():\n    assert False, 'intentional'\n")
    request, deliverable = await _make_deliverable(db_session, mock_tenant_id)
    await transition_request(request=request, target=RequestStatus.running, session=db_session)
    await transition_request(request=request, target=RequestStatus.review_ready, session=db_session)

    await run_verification(
        deliverable=deliverable,
        workspace_root=tmp_path,
        changed_files=["test_demo.py"],
        session=db_session,
    )

    await db_session.refresh(deliverable)
    assert deliverable.proof_state == ProofState.verification_failed
    with pytest.raises(GreenfieldStateError):
        await transition_request(request=request, target=RequestStatus.shipped, session=db_session)


@pytest.mark.asyncio
async def test_run_verification_with_no_applicable_aspects_human_review(
    tmp_path, db_session, mock_tenant_id, seeded_tenant
):
    (tmp_path / "README.md").write_text("# docs only\n")
    _, deliverable = await _make_deliverable(db_session, mock_tenant_id, deliverable_type=DeliverableType.doc)

    await run_verification(
        deliverable=deliverable,
        workspace_root=tmp_path,
        changed_files=["README.md"],
        session=db_session,
    )

    await db_session.refresh(deliverable)
    assert deliverable.proof_state == ProofState.human_review_required
    aspects = (
        (
            await db_session.execute(
                select(VerificationAspect).where(VerificationAspect.deliverable_id == deliverable.id)
            )
        )
        .scalars()
        .all()
    )
    assert aspects == []
