"""Pin: ``RunSummary`` Pydantic round-trip + ``ExecutionRun.run_summary``
ORM column accept JSON dicts and survive ``json.dumps`` -> ``json.loads``.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from backend.src.core.llm.reply_quality import ReplyQualityKind
from backend.src.models import ExecutionRun, Project, Request, RequestStatus, RunStatus
from backend.src.schemas.run_summary import RoundSummary, RunSummary


def test_run_summary_roundtrip_via_json() -> None:
    payload = RunSummary(
        total_rounds=3,
        total_tool_calls=4,
        per_round=[
            RoundSummary(
                round_idx=0,
                content_chars=120,
                tool_call_count=2,
                reply_quality=ReplyQualityKind.real_tool_calls,
                finish_reason="tool_calls",
            ),
            RoundSummary(
                round_idx=1,
                content_chars=0,
                tool_call_count=0,
                reply_quality=ReplyQualityKind.empty,
                finish_reason="stop",
            ),
        ],
        dominant_reply_quality=ReplyQualityKind.real_tool_calls,
        did_emit_fenced_block=True,
        files_actually_written=["add.py", "tests/test_add.py"],
        failure_signals=[],
    )
    encoded = payload.model_dump(mode="json")
    decoded = RunSummary.model_validate(json.loads(json.dumps(encoded)))
    assert decoded.total_rounds == 3
    assert decoded.dominant_reply_quality is ReplyQualityKind.real_tool_calls
    assert decoded.per_round[0].tool_call_count == 2
    assert decoded.files_actually_written == ["add.py", "tests/test_add.py"]


def test_run_summary_rejects_negative_counts() -> None:
    """``Field(ge=0)`` blocks nonsense values from leaking in via API."""
    with pytest.raises(ValueError):
        RunSummary(
            total_rounds=-1,
            total_tool_calls=0,
            per_round=[],
            dominant_reply_quality=ReplyQualityKind.empty,
        )


def test_run_summary_extra_slot_is_optional() -> None:
    out = RunSummary(
        total_rounds=0,
        total_tool_calls=0,
        per_round=[],
        dominant_reply_quality=ReplyQualityKind.empty,
    )
    assert out.extra is None


@pytest.mark.asyncio
async def test_execution_run_run_summary_column_persists_dict(db_session, mock_tenant_id, seeded_tenant) -> None:
    project = Project(tenant_id=mock_tenant_id, name="P", description="")
    db_session.add(project)
    await db_session.flush()
    request = Request(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        intent_summary="x",
        status=RequestStatus.open,
    )
    db_session.add(request)
    await db_session.flush()
    summary_payload = {
        "total_rounds": 2,
        "total_tool_calls": 1,
        "per_round": [
            {
                "round_idx": 0,
                "content_chars": 50,
                "tool_call_count": 1,
                "reply_quality": "real_tool_calls",
                "finish_reason": "tool_calls",
            },
        ],
        "dominant_reply_quality": "real_tool_calls",
        "did_emit_fenced_block": False,
        "files_actually_written": ["a.py"],
        "failure_signals": [],
    }
    run = ExecutionRun(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        request_id=request.id,
        status=RunStatus.done,
        run_summary=summary_payload,
    )
    db_session.add(run)
    await db_session.commit()

    fetched = (await db_session.execute(select(ExecutionRun).where(ExecutionRun.id == run.id))).scalar_one()
    assert fetched.run_summary is not None
    assert fetched.run_summary["dominant_reply_quality"] == "real_tool_calls"
    assert fetched.run_summary["per_round"][0]["tool_call_count"] == 1


@pytest.mark.asyncio
async def test_execution_run_run_summary_defaults_null(db_session, mock_tenant_id, seeded_tenant) -> None:
    project = Project(tenant_id=mock_tenant_id, name="P", description="")
    db_session.add(project)
    await db_session.flush()
    request = Request(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        intent_summary="x",
        status=RequestStatus.open,
    )
    db_session.add(request)
    await db_session.flush()
    run = ExecutionRun(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        request_id=request.id,
        status=RunStatus.pending,
    )
    db_session.add(run)
    await db_session.commit()

    fetched = (await db_session.execute(select(ExecutionRun).where(ExecutionRun.id == run.id))).scalar_one()
    assert fetched.run_summary is None
