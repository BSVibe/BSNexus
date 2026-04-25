"""Workers API — install-token lifecycle + worker list/revoke."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest


AUTH = {"Authorization": "Bearer fake"}


@pytest.mark.asyncio
async def test_install_token_initial_state(client):
    resp = await client.get("/api/v1/workers/install-token", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json() == {"has_token": False}


@pytest.mark.asyncio
async def test_generate_install_token_returns_token_once(client):
    resp = await client.post("/api/v1/workers/install-token", headers=AUTH)
    assert resp.status_code == 201
    body = resp.json()
    assert body["has_token"] is True
    assert isinstance(body["token"], str) and len(body["token"]) >= 32

    # Subsequent GET shows it's set but never returns the raw value.
    status_resp = await client.get("/api/v1/workers/install-token", headers=AUTH)
    assert status_resp.json() == {"has_token": True}


@pytest.mark.asyncio
async def test_regenerate_replaces_previous_token(client):
    first = (await client.post("/api/v1/workers/install-token", headers=AUTH)).json()
    second = (await client.post("/api/v1/workers/install-token", headers=AUTH)).json()
    assert first["token"] != second["token"]


@pytest.mark.asyncio
async def test_revoke_clears_token(client):
    await client.post("/api/v1/workers/install-token", headers=AUTH)
    resp = await client.delete("/api/v1/workers/install-token", headers=AUTH)
    assert resp.status_code == 204

    status_resp = await client.get("/api/v1/workers/install-token", headers=AUTH)
    assert status_resp.json() == {"has_token": False}


@pytest.mark.asyncio
async def test_list_workers_empty(client):
    resp = await client.get("/api/v1/workers", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_workers_returns_tenant_rows(client, db_session, mock_tenant_id):
    from backend.src.models import Worker

    db_session.add(
        Worker(
            tenant_id=mock_tenant_id,
            name="local-dev",
            labels=["dev"],
            status="online",
            capabilities=["claude_code"],
            token_hash="t1",
            last_heartbeat=datetime.now(timezone.utc),
        )
    )
    db_session.add(
        Worker(
            tenant_id=mock_tenant_id,
            name="ci-runner",
            labels=[],
            status="offline",
            capabilities=[],
            token_hash="t2",
        )
    )
    await db_session.commit()

    rows = (await client.get("/api/v1/workers", headers=AUTH)).json()
    names = {r["name"] for r in rows}
    assert names == {"local-dev", "ci-runner"}
    for r in rows:
        assert "token_hash" not in r  # never leak hash


@pytest.mark.asyncio
async def test_list_workers_filters_by_tenant(client, db_session):
    from backend.src.models import Tenant, Worker

    other_tid = uuid.uuid4()
    db_session.add(
        Tenant(
            id=other_tid,
            name="Other",
            slug=f"o-{uuid.uuid4().hex[:8]}",
            owner_user_id="x",
        )
    )
    await db_session.commit()
    db_session.add(
        Worker(
            tenant_id=other_tid,
            name="foreign",
            labels=[],
            status="online",
            capabilities=[],
            token_hash="fx",
        )
    )
    await db_session.commit()

    rows = (await client.get("/api/v1/workers", headers=AUTH)).json()
    assert rows == []


@pytest.mark.asyncio
async def test_delete_worker(client, db_session, mock_tenant_id):
    from backend.src.models import Worker

    w = Worker(
        tenant_id=mock_tenant_id,
        name="disposable",
        labels=[],
        status="offline",
        capabilities=[],
        token_hash="th",
    )
    db_session.add(w)
    await db_session.commit()
    await db_session.refresh(w)

    resp = await client.delete(f"/api/v1/workers/{w.id}", headers=AUTH)
    assert resp.status_code == 204

    rows = (await client.get("/api/v1/workers", headers=AUTH)).json()
    assert all(r["name"] != "disposable" for r in rows)


@pytest.mark.asyncio
async def test_delete_worker_404_for_foreign_tenant(client, db_session):
    from backend.src.models import Tenant, Worker

    other_tid = uuid.uuid4()
    db_session.add(
        Tenant(
            id=other_tid,
            name="Other",
            slug=f"o-{uuid.uuid4().hex[:8]}",
            owner_user_id="x",
        )
    )
    await db_session.commit()
    w = Worker(
        tenant_id=other_tid,
        name="hidden",
        labels=[],
        status="offline",
        capabilities=[],
        token_hash="th",
    )
    db_session.add(w)
    await db_session.commit()
    await db_session.refresh(w)

    resp = await client.delete(f"/api/v1/workers/{w.id}", headers=AUTH)
    assert resp.status_code == 404
