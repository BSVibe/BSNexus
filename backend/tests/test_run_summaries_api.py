"""Pin: ``GET /api/v1/run-summaries`` contract.

Two query modes:
- list: per-run summaries within the time window, optional project filter.
- aggregate: counts per ``dominant_reply_quality``.

Tenant-scoped — never returns another tenant's runs.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend.src.models import ExecutionRun, Project, Request, RequestStatus, RunStatus


async def _seed(db_session, tenant_id, *, kind: str = "real_tool_calls", days_ago: int = 0) -> ExecutionRun:
    project = Project(tenant_id=tenant_id, name=f"P-{uuid.uuid4().hex[:6]}", description="")
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
        run_summary={
            "total_rounds": 2,
            "total_tool_calls": 1,
            "per_round": [],
            "dominant_reply_quality": kind,
            "did_emit_fenced_block": False,
            "files_actually_written": [],
            "failure_signals": [],
        },
    )
    if days_ago > 0:
        run.created_at = datetime.now(timezone.utc) - timedelta(days=days_ago)
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)
    return run


@pytest.mark.asyncio
async def test_list_returns_run_summaries_for_tenant(client, db_session, mock_tenant_id, seeded_tenant) -> None:
    run = await _seed(db_session, mock_tenant_id, kind="real_tool_calls")
    resp = await client.get(
        "/api/v1/run-summaries",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert isinstance(payload, list)
    assert len(payload) == 1
    item = payload[0]
    assert item["run_id"] == str(run.id)
    assert item["summary"]["dominant_reply_quality"] == "real_tool_calls"


@pytest.mark.asyncio
async def test_list_filters_by_project_id(client, db_session, mock_tenant_id, seeded_tenant) -> None:
    run_a = await _seed(db_session, mock_tenant_id)
    run_b = await _seed(db_session, mock_tenant_id)
    resp = await client.get(
        f"/api/v1/run-summaries?project_id={run_a.project_id}",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["run_id"] == str(run_a.id)
    # Run B's project not returned.
    assert body[0]["run_id"] != str(run_b.id)


@pytest.mark.asyncio
async def test_list_excludes_runs_outside_window(client, db_session, mock_tenant_id, seeded_tenant) -> None:
    """A run created 10 days ago is excluded by default ``days=7``."""
    await _seed(db_session, mock_tenant_id, days_ago=10)
    resp = await client.get(
        "/api/v1/run-summaries?days=7",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_aggregate_returns_counts_per_kind(client, db_session, mock_tenant_id, seeded_tenant) -> None:
    await _seed(db_session, mock_tenant_id, kind="real_tool_calls")
    await _seed(db_session, mock_tenant_id, kind="real_tool_calls")
    await _seed(db_session, mock_tenant_id, kind="pseudocode_in_chat")
    resp = await client.get(
        "/api/v1/run-summaries?aggregate=true",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_runs"] == 3
    assert body["counts"] == {"real_tool_calls": 2, "pseudocode_in_chat": 1}


@pytest.mark.asyncio
async def test_aggregate_window_is_validated(client, mock_user, seeded_tenant) -> None:
    resp = await client.get(
        "/api/v1/run-summaries?days=0",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_unauthorized_request_returns_401(test_app) -> None:
    """Endpoint requires auth — no Authorization header → 401."""
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as anon:
        resp = await anon.get("/api/v1/run-summaries")
    assert resp.status_code == 401
