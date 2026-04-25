"""Inside panel APIs — read-only ExecutionRun + CompositionSnapshot tree."""

from __future__ import annotations

import uuid

import pytest

from backend.src.models import (
    CompositionSnapshot,
    CompositionSource,
    ExecutionRun,
    Request,
    RunStatus,
)


async def _make_project(client, name: str = "P") -> str:
    resp = await client.post(
        "/api/v1/projects",
        json={"name": name},
        headers={"Authorization": "Bearer fake"},
    )
    return resp.json()["id"]


async def _seed_request(db_session, project_id: uuid.UUID, tenant_id: uuid.UUID) -> Request:
    r = Request(
        tenant_id=tenant_id,
        project_id=project_id,
        intent_summary="Ship feature",
    )
    db_session.add(r)
    await db_session.commit()
    await db_session.refresh(r)
    return r


@pytest.mark.asyncio
async def test_list_runs_for_request_empty(client, db_session, mock_tenant_id):
    pid = uuid.UUID(await _make_project(client))
    req = await _seed_request(db_session, pid, mock_tenant_id)

    resp = await client.get(
        f"/api/v1/requests/{req.id}/runs",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_runs_returns_runs_in_tree_order(client, db_session, mock_tenant_id):
    pid = uuid.UUID(await _make_project(client))
    req = await _seed_request(db_session, pid, mock_tenant_id)

    parent = ExecutionRun(
        tenant_id=mock_tenant_id,
        project_id=pid,
        request_id=req.id,
        status=RunStatus.done,
    )
    db_session.add(parent)
    await db_session.commit()
    await db_session.refresh(parent)

    child = ExecutionRun(
        tenant_id=mock_tenant_id,
        project_id=pid,
        request_id=req.id,
        parent_run_id=parent.id,
        status=RunStatus.pending,
    )
    db_session.add(child)
    await db_session.commit()

    rows = (
        await client.get(
            f"/api/v1/requests/{req.id}/runs",
            headers={"Authorization": "Bearer fake"},
        )
    ).json()
    assert len(rows) == 2
    # Older (parent) first.
    assert rows[0]["parent_run_id"] is None
    assert rows[1]["parent_run_id"] == rows[0]["id"]


@pytest.mark.asyncio
async def test_list_runs_404_for_foreign_tenant(client, db_session):
    other_tid = uuid.uuid4()
    from backend.src.models import Project, Tenant

    db_session.add(
        Tenant(
            id=other_tid,
            name="Other",
            slug=f"o-{uuid.uuid4().hex[:8]}",
            owner_user_id="x",
        )
    )
    await db_session.commit()

    p = Project(tenant_id=other_tid, name="Hidden", description="")
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)

    req = Request(
        tenant_id=other_tid,
        project_id=p.id,
        intent_summary="secret",
    )
    db_session.add(req)
    await db_session.commit()
    await db_session.refresh(req)

    resp = await client.get(
        f"/api/v1/requests/{req.id}/runs",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_composition_snapshot(client, db_session, mock_tenant_id):
    pid = uuid.UUID(await _make_project(client))
    req = await _seed_request(db_session, pid, mock_tenant_id)

    snap = CompositionSnapshot(
        tenant_id=mock_tenant_id,
        request_id=req.id,
        source=CompositionSource.local,
        system_prompt_ref={"inline": "You are an engineer"},
        tools_allowed=["read"],
        context_doc_refs=[],
        persona_label="builder",
    )
    db_session.add(snap)
    await db_session.commit()
    await db_session.refresh(snap)

    resp = await client.get(
        f"/api/v1/composition-snapshots/{snap.id}",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "local"
    assert body["persona_label"] == "builder"
    assert body["system_prompt_ref"] == {"inline": "You are an engineer"}


@pytest.mark.asyncio
async def test_get_composition_snapshot_404_for_foreign_tenant(client, db_session):
    other_tid = uuid.uuid4()
    from backend.src.models import Project, Tenant

    db_session.add(
        Tenant(
            id=other_tid,
            name="Other",
            slug=f"o-{uuid.uuid4().hex[:8]}",
            owner_user_id="x",
        )
    )
    await db_session.commit()

    p = Project(tenant_id=other_tid, name="Hidden", description="")
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)

    req = Request(tenant_id=other_tid, project_id=p.id, intent_summary="x")
    db_session.add(req)
    await db_session.commit()
    await db_session.refresh(req)

    snap = CompositionSnapshot(
        tenant_id=other_tid,
        request_id=req.id,
        source=CompositionSource.local,
        system_prompt_ref={},
        tools_allowed=[],
        context_doc_refs=[],
        persona_label="secret",
    )
    db_session.add(snap)
    await db_session.commit()
    await db_session.refresh(snap)

    resp = await client.get(
        f"/api/v1/composition-snapshots/{snap.id}",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 404
