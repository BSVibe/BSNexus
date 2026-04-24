"""harness.refresh_context — materialises workspace + history for the next run."""

from __future__ import annotations

import uuid

import pytest

from backend.src.core import harness, project_workspace
from backend.src.models import (
    ConversationMessage,
    ExecutionRun,
    Project,
    Request,
    RequestStatus,
    RunPriority,
    RunStatus,
)


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(project_workspace, "_root", lambda: tmp_path)
    return tmp_path


async def _seed_request(db_session, tenant_id) -> tuple[Project, Request]:
    project = Project(tenant_id=tenant_id, name="ctx test", description="")
    db_session.add(project)
    await db_session.flush()
    req = Request(
        tenant_id=tenant_id,
        project_id=project.id,
        intent_summary="간단한 TODO 웹앱 만들어줘.",
        status=RequestStatus.open,
    )
    db_session.add(req)
    await db_session.commit()
    await db_session.refresh(project)
    await db_session.refresh(req)
    return project, req


@pytest.mark.asyncio
async def test_refresh_context_writes_expected_files(
    db_session, mock_tenant_id, seeded_tenant, isolated_workspace
):
    project, req = await _seed_request(db_session, mock_tenant_id)

    # Pretend phase 1 wrote some files.
    project_workspace.write_file(project.id, "src/app/page.tsx", "export default function Home(){}")
    project_workspace.write_file(project.id, "package.json", '{"name":"x"}')

    await harness.refresh_context(project.id, request=req, db=db_session)

    ctx_root = harness.context_dir(project.id)
    workspace_md = (ctx_root / "workspace.md").read_text()
    history_md = (ctx_root / "history.md").read_text()

    assert "src/app/page.tsx" in workspace_md
    assert "package.json" in workspace_md
    # Current-state listing, not a stale copy:
    assert workspace_md.startswith("# Workspace")

    assert "간단한 TODO 웹앱" in history_md
    assert "(no prior phases" in history_md  # no completed runs yet


@pytest.mark.asyncio
async def test_refresh_context_history_includes_prior_phase_summaries(
    db_session, mock_tenant_id, seeded_tenant
):
    project, req = await _seed_request(db_session, mock_tenant_id)

    # A prior phase completed — its run + assistant reply exist.
    run = ExecutionRun(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        request_id=req.id,
        status=RunStatus.done,
        priority=RunPriority.medium,
        directive="Phase 1: scaffold Next.js app.",
        output_ref={"inline": "Next.js 프로젝트를 초기화했습니다.\n\n파일: package.json, src/app/layout.tsx", "files": []},
    )
    db_session.add(run)
    await db_session.flush()

    reply = ConversationMessage(
        project_id=project.id,
        role="assistant",
        content="Next.js 프로젝트를 초기화했습니다.\n\n파일: package.json, src/app/layout.tsx",
        request_id=req.id,
    )
    db_session.add(reply)
    await db_session.commit()

    await harness.refresh_context(project.id, request=req, db=db_session)

    history_md = (harness.context_dir(project.id) / "history.md").read_text()
    assert "Phase 1" in history_md
    assert "scaffold Next.js" in history_md
    assert "Next.js 프로젝트를 초기화했습니다" in history_md


def test_write_stack_contract_idempotent_and_noop_on_empty(isolated_workspace):
    project_id = uuid.uuid4()
    harness.write_stack_contract(project_id, "")
    stack_path = harness.context_dir(project_id) / "stack.md"
    assert not stack_path.exists()

    harness.write_stack_contract(
        project_id,
        "Next.js 14 App Router (src/app/), Prisma+SQLite, Tailwind, TypeScript",
    )
    assert stack_path.exists()
    content = stack_path.read_text()
    assert "Stack Contract" in content
    assert "Next.js 14 App Router" in content


def test_list_files_hides_harness_by_default(isolated_workspace):
    project_id = uuid.uuid4()
    project_workspace.write_file(project_id, "src/main.py", "x = 1")
    harness.write_stack_contract(project_id, "Python stdlib CLI")

    default = project_workspace.list_files(project_id)
    assert [e["path"] for e in default] == ["src/main.py"]

    with_hidden = project_workspace.list_files(project_id, include_hidden=True)
    paths = sorted(e["path"] for e in with_hidden)
    assert ".bsnexus/context/stack.md" in paths
    assert "src/main.py" in paths
