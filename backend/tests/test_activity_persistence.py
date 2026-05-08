"""Pin: ``persist_tool_activity_log`` translates an adapter's
in-memory activity log into ``ExecutionRunActivity`` rows."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from backend.src.core.llm.activity_persistence import (
    build_activity_rows,
    persist_tool_activity_log,
)
from backend.src.models import ExecutionRun, ExecutionRunActivity, Project, Request, RequestStatus, RunStatus
from backend.src.models.execution_run_activity import ActivityLevel


async def _seed_run(db_session, tenant_id: uuid.UUID) -> ExecutionRun:
    project = Project(tenant_id=tenant_id, name="P", description="")
    db_session.add(project)
    await db_session.flush()
    request = Request(
        tenant_id=tenant_id,
        project_id=project.id,
        intent_summary="x",
        status=RequestStatus.open,
    )
    db_session.add(request)
    await db_session.flush()
    run = ExecutionRun(
        tenant_id=tenant_id,
        project_id=project.id,
        request_id=request.id,
        status=RunStatus.running,
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)
    return run


def test_build_activity_rows_returns_empty_for_empty_log() -> None:
    fake_run = ExecutionRun(id=uuid.uuid4(), project_id=uuid.uuid4(), tenant_id=uuid.uuid4(), status=RunStatus.done)
    assert build_activity_rows(fake_run, []) == []


def test_build_activity_rows_skips_non_dict_records() -> None:
    fake_run = ExecutionRun(id=uuid.uuid4(), project_id=uuid.uuid4(), tenant_id=uuid.uuid4(), status=RunStatus.done)
    log = ["not a dict", 42, None]  # type: ignore[list-item]
    assert build_activity_rows(fake_run, log) == []  # type: ignore[arg-type]


def test_build_activity_rows_maps_tool_call_kinds_to_tool_level() -> None:
    fake_run = ExecutionRun(id=uuid.uuid4(), project_id=uuid.uuid4(), tenant_id=uuid.uuid4(), status=RunStatus.done)
    log = [
        {
            "kind": "tool_call_start",
            "round_idx": 0,
            "tool_name": "file_write",
            "tool_call_id": "c1",
            "args": '{"path": "x"}',
            "occurred_at": "2026-05-08T15:00:00+00:00",
        },
        {
            "kind": "tool_call_done",
            "round_idx": 0,
            "tool_name": "file_write",
            "tool_call_id": "c1",
            "outcome": "ok",
            "duration_ms": 120,
            "occurred_at": "2026-05-08T15:00:01+00:00",
        },
    ]
    rows = build_activity_rows(fake_run, log)
    assert len(rows) == 2
    assert rows[0].level == ActivityLevel.tool
    assert rows[0].event_type == "tool_call_start"
    assert "file_write" in rows[0].summary
    assert rows[0].detail["round_idx"] == 0
    assert rows[1].level == ActivityLevel.tool
    assert rows[1].event_type == "tool_call_done"
    assert "120ms" in rows[1].summary


def test_build_activity_rows_maps_llm_round_complete_to_milestone() -> None:
    fake_run = ExecutionRun(id=uuid.uuid4(), project_id=uuid.uuid4(), tenant_id=uuid.uuid4(), status=RunStatus.done)
    log = [
        {
            "kind": "llm_round_complete",
            "round_idx": 2,
            "content_chars": 423,
            "tool_call_count": 3,
            "finish_reason": "tool_calls",
            "occurred_at": "2026-05-08T15:00:02+00:00",
        }
    ]
    rows = build_activity_rows(fake_run, log)
    assert len(rows) == 1
    assert rows[0].level == ActivityLevel.milestone
    assert rows[0].event_type == "llm_round_complete"
    assert rows[0].detail["content_chars"] == 423
    # ``kind`` and ``occurred_at`` are not duplicated into detail.
    assert "kind" not in rows[0].detail
    assert "occurred_at" not in rows[0].detail


def test_build_activity_rows_skips_record_without_kind() -> None:
    fake_run = ExecutionRun(id=uuid.uuid4(), project_id=uuid.uuid4(), tenant_id=uuid.uuid4(), status=RunStatus.done)
    log = [{"round_idx": 0, "tool_name": "file_write"}]  # missing kind
    assert build_activity_rows(fake_run, log) == []


def test_build_activity_rows_handles_invalid_occurred_at() -> None:
    """Bad timestamp string falls back to default created_at — does
    NOT crash the row build."""
    fake_run = ExecutionRun(id=uuid.uuid4(), project_id=uuid.uuid4(), tenant_id=uuid.uuid4(), status=RunStatus.done)
    log = [
        {
            "kind": "tool_call_done",
            "round_idx": 0,
            "tool_name": "file_write",
            "occurred_at": "not-a-timestamp",
        }
    ]
    rows = build_activity_rows(fake_run, log)
    assert len(rows) == 1
    # row.created_at is None until inserted; the server_default will fill it.


@pytest.mark.asyncio
async def test_persist_tool_activity_log_writes_rows_to_db(db_session, mock_tenant_id, seeded_tenant) -> None:
    run = await _seed_run(db_session, mock_tenant_id)
    log = [
        {
            "kind": "tool_call_start",
            "round_idx": 0,
            "tool_name": "file_write",
            "tool_call_id": "c1",
            "args": '{"path": "x"}',
            "occurred_at": "2026-05-08T15:00:00+00:00",
        },
        {
            "kind": "tool_call_done",
            "round_idx": 0,
            "tool_name": "file_write",
            "tool_call_id": "c1",
            "outcome": "ok",
            "duration_ms": 120,
            "occurred_at": "2026-05-08T15:00:01+00:00",
        },
    ]

    n = await persist_tool_activity_log(run, log, db_session)
    await db_session.commit()
    assert n == 2

    rows = (
        (await db_session.execute(select(ExecutionRunActivity).where(ExecutionRunActivity.run_id == run.id)))
        .scalars()
        .all()
    )
    assert len(rows) == 2
    kinds = sorted(r.event_type for r in rows)
    assert kinds == ["tool_call_done", "tool_call_start"]
    assert all(r.level == ActivityLevel.tool for r in rows)


@pytest.mark.asyncio
async def test_persist_tool_activity_log_empty_log_writes_nothing(db_session, mock_tenant_id, seeded_tenant) -> None:
    run = await _seed_run(db_session, mock_tenant_id)
    n = await persist_tool_activity_log(run, [], db_session)
    assert n == 0
    rows = (
        (await db_session.execute(select(ExecutionRunActivity).where(ExecutionRunActivity.run_id == run.id)))
        .scalars()
        .all()
    )
    assert rows == []
