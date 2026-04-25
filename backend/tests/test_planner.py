"""Replanner — JSON parse + fallback + decision short-circuit."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from backend.src.core.planner import (
    ReplanResult,
    _parse_replan,
    replan_next_step,
)


# ---------- _parse_replan -----------------------------------------------------


def test_parse_next_step():
    raw = json.dumps(
        {
            "decision": "next_step",
            "founder_message": "다음으로 시장 조사 시작",
            "phase_name": "Market Research",
            "phase_direction": "Survey 5 competitors and write findings.md",
        }
    )
    out = _parse_replan(raw)
    assert isinstance(out, ReplanResult)
    assert out.decision == "next_step"
    assert "시장 조사" in out.founder_message
    assert out.phase_name == "Market Research"
    assert "competitors" in (out.phase_direction or "")


def test_parse_done():
    raw = json.dumps({"decision": "done", "founder_message": "Goal 마무리됐어요. 산출물 5개."})
    out = _parse_replan(raw)
    assert out is not None
    assert out.decision == "done"
    assert "마무리" in out.founder_message
    assert out.phase_direction is None


def test_parse_ask_founder():
    raw = json.dumps(
        {
            "decision": "ask_founder",
            "founder_message": "갈림길이에요",
            "question": "Magic link로 갈까요, OAuth로 갈까요?",
            "options": ["magic link", "OAuth"],
            "blocking": True,
        }
    )
    out = _parse_replan(raw)
    assert out is not None
    assert out.decision == "ask_founder"
    assert out.question and "Magic link" in out.question
    assert out.options == ["magic link", "OAuth"]
    assert out.blocking is True


def test_parse_strips_markdown_fence():
    raw = '```json\n{"decision":"done","founder_message":"ok"}\n```'
    out = _parse_replan(raw)
    assert out is not None
    assert out.decision == "done"


def test_parse_extracts_from_prose_wrapper():
    raw = 'Sure, here:\n{"decision":"done","founder_message":"ok"}\nthanks'
    out = _parse_replan(raw)
    assert out is not None
    assert out.decision == "done"


def test_parse_rejects_unknown_decision():
    raw = json.dumps({"decision": "delegate", "founder_message": "x"})
    assert _parse_replan(raw) is None


def test_parse_rejects_next_step_without_direction():
    raw = json.dumps({"decision": "next_step", "founder_message": "go"})
    assert _parse_replan(raw) is None


def test_parse_rejects_ask_without_question():
    raw = json.dumps({"decision": "ask_founder", "founder_message": "..."})
    assert _parse_replan(raw) is None


def test_parse_rejects_empty_message():
    raw = json.dumps({"decision": "done", "founder_message": ""})
    assert _parse_replan(raw) is None


def test_parse_rejects_garbage():
    assert _parse_replan("not json") is None
    assert _parse_replan("") is None


# ---------- replan_next_step --------------------------------------------------


@pytest.mark.asyncio
async def test_replan_falls_back_to_intent_when_no_llm_configured(db_session, mock_tenant_id, seeded_tenant):
    """When no ExecutorConfig is_selected for the tenant, replanner skips
    the LLM and uses the founder's intent as the first iteration directly."""
    from backend.src.models import Project, Request, RequestStatus

    project = Project(tenant_id=mock_tenant_id, name="t", description="")
    db_session.add(project)
    await db_session.flush()
    req = Request(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        intent_summary="Build a TODO app",
        status=RequestStatus.open,
    )
    db_session.add(req)
    await db_session.commit()

    out = await replan_next_step(
        request=req,
        completed_runs=[],
        pending_decisions=[],
        recent_messages=[],
        tenant_id=mock_tenant_id,
        session=db_session,
    )
    assert out.decision == "next_step"
    assert "Build a TODO app" in (out.phase_direction or "")


@pytest.mark.asyncio
async def test_replan_no_llm_after_first_iteration_says_done(db_session, mock_tenant_id, seeded_tenant):
    """Without an LLM, second+ iterations have no way to decide adaptively
    — declare done so we don't loop."""
    from backend.src.models import (
        ExecutionRun,
        Project,
        Request,
        RequestStatus,
        RunPriority,
        RunStatus,
    )

    project = Project(tenant_id=mock_tenant_id, name="t", description="")
    db_session.add(project)
    await db_session.flush()
    req = Request(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        intent_summary="Do thing",
        status=RequestStatus.open,
    )
    db_session.add(req)
    await db_session.flush()
    prior = ExecutionRun(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        request_id=req.id,
        status=RunStatus.done,
        priority=RunPriority.medium,
        directive="iter 1",
        output_ref={"founder_summary": "did the thing"},
    )
    db_session.add(prior)
    await db_session.commit()

    out = await replan_next_step(
        request=req,
        completed_runs=[prior],
        pending_decisions=[],
        recent_messages=[],
        tenant_id=mock_tenant_id,
        session=db_session,
    )
    assert out.decision == "done"


@pytest.mark.asyncio
async def test_replan_short_circuits_on_blocking_decision(db_session, mock_tenant_id, seeded_tenant):
    """If a blocking decision is unresolved, the replanner doesn't even
    talk to the LLM — it returns ask_founder so the orchestrator can
    re-surface the question."""
    from backend.src.models import (
        Decision,
        Project,
        Request,
        RequestStatus,
    )

    project = Project(tenant_id=mock_tenant_id, name="t", description="")
    db_session.add(project)
    await db_session.flush()
    req = Request(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        intent_summary="x",
        status=RequestStatus.open,
    )
    db_session.add(req)
    await db_session.flush()
    decision = Decision(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        request_id=req.id,
        question="Auth: magic link or OAuth?",
        options=["magic link", "OAuth"],
        blocking=True,
    )
    db_session.add(decision)
    await db_session.commit()

    out = await replan_next_step(
        request=req,
        completed_runs=[],
        pending_decisions=[decision],
        recent_messages=[],
        tenant_id=mock_tenant_id,
        session=db_session,
    )
    assert out.decision == "ask_founder"
    assert out.question and "Auth" in out.question


@pytest.mark.asyncio
async def test_replan_calls_llm_when_configured(db_session, mock_tenant_id, seeded_tenant):
    """LLM is configured → replanner serializes context to the LLM and
    parses the JSON response."""
    from backend.src.models import (
        ExecutorConfig,
        Project,
        Request,
        RequestStatus,
    )

    project = Project(tenant_id=mock_tenant_id, name="t", description="")
    db_session.add(project)
    await db_session.flush()
    req = Request(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        intent_summary="시장 조사해서 제안",
        status=RequestStatus.open,
    )
    db_session.add(req)
    db_session.add(
        ExecutorConfig(
            tenant_id=mock_tenant_id,
            name="local",
            executor_type="generic_llm",
            config={"model": "ollama_chat/x", "api_key": "k", "base_url": "http://127.0.0.1"},
            is_selected=True,
        )
    )
    await db_session.commit()

    fake_response = json.dumps(
        {
            "decision": "next_step",
            "founder_message": "시장 조사부터 시작합니다",
            "phase_name": "Market Research",
            "phase_direction": "Survey competitors A/B/C and write findings.md",
        }
    )
    with patch(
        "backend.src.core.planner._run_replanner_llm",
        new=AsyncMock(return_value=fake_response),
    ):
        out = await replan_next_step(
            request=req,
            completed_runs=[],
            pending_decisions=[],
            recent_messages=[],
            tenant_id=mock_tenant_id,
            session=db_session,
        )
    assert out.decision == "next_step"
    assert out.phase_name == "Market Research"
    assert "시장 조사" in out.founder_message


@pytest.mark.asyncio
async def test_replan_falls_back_when_llm_returns_garbage(db_session, mock_tenant_id, seeded_tenant):
    from backend.src.models import (
        ExecutorConfig,
        Project,
        Request,
        RequestStatus,
    )

    project = Project(tenant_id=mock_tenant_id, name="t", description="")
    db_session.add(project)
    await db_session.flush()
    req = Request(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        intent_summary="Build X",
        status=RequestStatus.open,
    )
    db_session.add(req)
    db_session.add(
        ExecutorConfig(
            tenant_id=mock_tenant_id,
            name="local",
            executor_type="generic_llm",
            config={"model": "ollama_chat/x", "api_key": "k", "base_url": "http://127.0.0.1"},
            is_selected=True,
        )
    )
    await db_session.commit()

    with patch(
        "backend.src.core.planner._run_replanner_llm",
        new=AsyncMock(return_value="totally not json"),
    ):
        out = await replan_next_step(
            request=req,
            completed_runs=[],
            pending_decisions=[],
            recent_messages=[],
            tenant_id=mock_tenant_id,
            session=db_session,
        )
    # Falls back to first-iteration default.
    assert out.decision == "next_step"
    assert "Build X" in (out.phase_direction or "")
