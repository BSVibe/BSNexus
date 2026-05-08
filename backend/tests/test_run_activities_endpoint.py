"""Pin: ``GET /api/v1/runs/{id}/activities`` returns the run's
activity rows in chronological order (PR7 TASK-006 backend prereq)."""

from __future__ import annotations

import pytest

from backend.src.models import ExecutionRun, ExecutionRunActivity, Project, Request, RequestStatus, RunStatus
from backend.src.models.execution_run_activity import ActivityLevel


async def _seed(db_session, tenant_id):
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
        status=RunStatus.done,
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)
    return run


@pytest.mark.asyncio
async def test_activities_endpoint_returns_chronological_rows(client, db_session, mock_tenant_id, seeded_tenant):
    run = await _seed(db_session, mock_tenant_id)
    db_session.add_all(
        [
            ExecutionRunActivity(
                run_id=run.id,
                project_id=run.project_id,
                level=ActivityLevel.milestone,
                event_type="llm_round_complete",
                summary="round 0: 100 chars, 1 tool call",
                detail={"round_idx": 0},
            ),
            ExecutionRunActivity(
                run_id=run.id,
                project_id=run.project_id,
                level=ActivityLevel.tool,
                event_type="tool_call_start",
                summary="[round 0] file_write start",
                detail={"round_idx": 0, "tool_name": "file_write"},
            ),
            ExecutionRunActivity(
                run_id=run.id,
                project_id=run.project_id,
                level=ActivityLevel.tool,
                event_type="tool_call_done",
                summary="[round 0] file_write → ok 120ms",
                detail={"round_idx": 0, "outcome": "ok"},
            ),
        ]
    )
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/runs/{run.id}/activities",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert len(payload) == 3
    event_types = [r["event_type"] for r in payload]
    # Insertion order matches our chronological add.
    assert event_types == ["llm_round_complete", "tool_call_start", "tool_call_done"]


@pytest.mark.asyncio
async def test_activities_endpoint_filters_by_level(client, db_session, mock_tenant_id, seeded_tenant):
    run = await _seed(db_session, mock_tenant_id)
    db_session.add_all(
        [
            ExecutionRunActivity(
                run_id=run.id,
                project_id=run.project_id,
                level=ActivityLevel.milestone,
                event_type="llm_round_complete",
                summary="m",
            ),
            ExecutionRunActivity(
                run_id=run.id,
                project_id=run.project_id,
                level=ActivityLevel.tool,
                event_type="tool_call_start",
                summary="t",
            ),
        ]
    )
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/runs/{run.id}/activities?level=tool",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["level"] == "tool"


@pytest.mark.asyncio
async def test_activities_endpoint_404s_for_unknown_run(client, db_session, mock_tenant_id, seeded_tenant):
    import uuid

    fake_id = uuid.uuid4()
    resp = await client.get(
        f"/api/v1/runs/{fake_id}/activities",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 404
