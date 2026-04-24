"""Planner — macro detection, JSON parsing, chain seeding."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from backend.src.core.planner import (
    ChainPlan,
    PhasePlan,
    _parse_plan,
    is_macro_direction,
    maybe_plan_phases,
    seed_phase_chain,
)
from backend.src.models import (
    ExecutionRun,
    ExecutorConfig,
    Project,
    Request,
    RequestStatus,
    RunPriority,
    RunStatus,
)


def test_macro_korean_app_request():
    assert is_macro_direction("TODO 앱 만들어줘.")
    assert is_macro_direction("간단한 웹사이트를 만들어 주세요")
    assert is_macro_direction("결제 시스템을 구현")


def test_macro_english_app_request():
    assert is_macro_direction("build a TODO app with React")
    assert is_macro_direction("create a new website")
    assert is_macro_direction("implement a billing service")


def test_non_macro_rejected():
    assert not is_macro_direction("")
    assert not is_macro_direction("short")
    assert not is_macro_direction("hi there")
    assert not is_macro_direction("change the button color")
    assert not is_macro_direction("what is 2+2?")


def test_parse_plan_extracts_stack_and_phases():
    raw = json.dumps(
        {
            "stack": "Next.js 14 App Router + Prisma + SQLite",
            "phases": [
                {"name": "init", "direction": "Initialize project."},
                {"name": "db", "direction": "Define schema."},
                {"name": "api", "direction": "Add routes."},
            ],
        }
    )
    plan = _parse_plan(raw)
    assert plan is not None
    assert "Next.js" in plan.stack
    assert [p.name for p in plan.phases] == ["init", "db", "api"]


def test_parse_plan_strips_markdown_fences():
    raw = '```json\n{"stack": "py", "phases": [{"name": "a", "direction": "do it"}]}\n```'
    plan = _parse_plan(raw)
    assert plan is not None
    assert plan.stack == "py"
    assert plan.phases[0].name == "a"


def test_parse_plan_extracts_from_prose_wrapper():
    raw = 'Here is your plan:\n{"phases":[{"name":"x","direction":"x"}]}\nThanks!'
    plan = _parse_plan(raw)
    assert plan is not None
    assert plan.phases[0].name == "x"


def test_parse_plan_rejects_garbage():
    assert _parse_plan("not json at all") is None
    assert _parse_plan("{malformed") is None
    assert _parse_plan('{"stack": "x"}') is None
    assert _parse_plan('{"phases": "not a list"}') is None
    assert _parse_plan('{"phases": []}') is None


def test_parse_plan_caps_phases_at_six():
    raw = json.dumps(
        {"phases": [{"name": f"p{i}", "direction": f"d{i}"} for i in range(10)]}
    )
    plan = _parse_plan(raw)
    assert plan is not None
    assert len(plan.phases) == 6


def test_parse_plan_names_default_when_blank():
    raw = json.dumps(
        {
            "phases": [
                {"name": "", "direction": "first"},
                {"name": "", "direction": "second"},
            ]
        }
    )
    plan = _parse_plan(raw)
    assert plan is not None
    assert plan.phases[0].name.startswith("phase")


def test_parse_plan_drops_phase_with_empty_direction():
    raw = json.dumps(
        {
            "phases": [
                {"name": "keep", "direction": "real"},
                {"name": "drop", "direction": ""},
            ]
        }
    )
    plan = _parse_plan(raw)
    assert plan is not None
    assert len(plan.phases) == 1
    assert plan.phases[0].name == "keep"


async def _seed_executor(db_session, tenant_id, executor_type="generic_llm", **cfg):
    base_cfg = {
        "model": "ollama_chat/test:latest",
        "api_key": "unused",
        "base_url": "http://localhost:11434",
    }
    base_cfg.update(cfg)
    row = ExecutorConfig(
        tenant_id=tenant_id,
        name="test",
        executor_type=executor_type,
        is_selected=True,
        config=base_cfg,
    )
    db_session.add(row)
    await db_session.commit()


@pytest.mark.asyncio
async def test_maybe_plan_phases_skips_non_macro(db_session, mock_tenant_id, seeded_tenant):
    assert await maybe_plan_phases(direction="hi", tenant_id=mock_tenant_id, session=db_session) is None


@pytest.mark.asyncio
async def test_maybe_plan_phases_skips_when_no_executor(
    db_session, mock_tenant_id, seeded_tenant
):
    plan = await maybe_plan_phases(
        direction="TODO 앱 만들어줘.", tenant_id=mock_tenant_id, session=db_session
    )
    assert plan is None


@pytest.mark.asyncio
async def test_maybe_plan_phases_returns_chain_on_success(
    db_session, mock_tenant_id, seeded_tenant
):
    await _seed_executor(db_session, mock_tenant_id)
    raw = json.dumps(
        {
            "stack": "Next.js",
            "phases": [
                {"name": "init", "direction": "Init."},
                {"name": "impl", "direction": "Do it."},
            ],
        }
    )
    with patch("backend.src.core.planner._run_planner_llm", AsyncMock(return_value=raw)):
        plan = await maybe_plan_phases(
            direction="TODO 앱 만들어줘.", tenant_id=mock_tenant_id, session=db_session
        )
    assert plan is not None
    assert plan.stack == "Next.js"
    assert len(plan.phases) == 2


@pytest.mark.asyncio
async def test_maybe_plan_phases_returns_none_on_llm_error(
    db_session, mock_tenant_id, seeded_tenant
):
    await _seed_executor(db_session, mock_tenant_id)
    with patch(
        "backend.src.core.planner._run_planner_llm",
        AsyncMock(side_effect=RuntimeError("connection refused")),
    ):
        plan = await maybe_plan_phases(
            direction="TODO 앱 만들어줘.", tenant_id=mock_tenant_id, session=db_session
        )
    assert plan is None


@pytest.mark.asyncio
async def test_maybe_plan_phases_rejects_single_phase_plan(
    db_session, mock_tenant_id, seeded_tenant
):
    await _seed_executor(db_session, mock_tenant_id)
    raw = json.dumps({"phases": [{"name": "only", "direction": "do it"}]})
    with patch("backend.src.core.planner._run_planner_llm", AsyncMock(return_value=raw)):
        plan = await maybe_plan_phases(
            direction="TODO 앱 만들어줘.", tenant_id=mock_tenant_id, session=db_session
        )
    assert plan is None


@pytest.mark.asyncio
async def test_maybe_plan_phases_handles_bsgateway_executor(
    db_session, mock_tenant_id, seeded_tenant
):
    await _seed_executor(
        db_session,
        mock_tenant_id,
        executor_type="bsgateway",
        model="openai/gpt-4o-mini",
        bsgateway_url="http://gw:8080",
        bsgateway_api_key="k",
    )
    raw = json.dumps(
        {"phases": [{"name": "a", "direction": "a"}, {"name": "b", "direction": "b"}]}
    )
    with patch(
        "backend.src.core.planner._run_planner_llm", AsyncMock(return_value=raw)
    ) as mock_llm:
        plan = await maybe_plan_phases(
            direction="build a new service",
            tenant_id=mock_tenant_id,
            session=db_session,
        )
    assert plan is not None
    kwargs = mock_llm.await_args.kwargs
    assert kwargs["base_url"] == "http://gw:8080"
    assert kwargs["api_key"] == "k"


async def _seed_root(db_session, tenant_id):
    project = Project(tenant_id=tenant_id, name="chain", description="")
    db_session.add(project)
    await db_session.flush()
    req = Request(
        tenant_id=tenant_id,
        project_id=project.id,
        intent_summary="scope",
        status=RequestStatus.open,
    )
    db_session.add(req)
    await db_session.flush()
    run = ExecutionRun(
        tenant_id=tenant_id,
        project_id=project.id,
        request_id=req.id,
        status=RunStatus.pending,
        priority=RunPriority.medium,
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)
    return run


@pytest.mark.asyncio
async def test_seed_phase_chain_rewrites_root_and_adds_blocked_successors(
    db_session, mock_tenant_id, seeded_tenant, tmp_path, monkeypatch
):
    monkeypatch.setattr("backend.src.core.project_workspace._root", lambda: tmp_path)
    root = await _seed_root(db_session, mock_tenant_id)

    plan = ChainPlan(
        stack="Next.js 14 + Prisma",
        phases=[
            PhasePlan(name="p1", direction="phase 1"),
            PhasePlan(name="p2", direction="phase 2"),
            PhasePlan(name="p3", direction="phase 3"),
        ],
    )
    created = await seed_phase_chain(session=db_session, root_run=root, plan=plan)

    assert len(created) == 3
    assert created[0].id == root.id
    assert created[0].directive == "phase 1"

    children = list(
        (
            await db_session.execute(
                select(ExecutionRun).where(ExecutionRun.parent_run_id.isnot(None))
            )
        ).scalars()
    )
    assert {r.status for r in children} == {RunStatus.blocked}

    stack_file = tmp_path / str(root.project_id) / ".bsnexus" / "context" / "stack.md"
    assert stack_file.exists()
    assert "Next.js 14" in stack_file.read_text()


@pytest.mark.asyncio
async def test_seed_phase_chain_empty_plan_is_noop(
    db_session, mock_tenant_id, seeded_tenant
):
    root = await _seed_root(db_session, mock_tenant_id)
    created = await seed_phase_chain(
        session=db_session, root_run=root, plan=ChainPlan(stack="", phases=[])
    )
    assert created == []
