"""Tests for Worker registration and heartbeat API."""

from __future__ import annotations

import uuid

import pytest

from backend.src.models import Tenant


_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


@pytest.fixture
async def tenant(db_session):
    t = Tenant(id=_TENANT_ID, name="Test", slug="test", owner_user_id="user-1")
    db_session.add(t)
    await db_session.flush()
    await db_session.commit()
    return t


class TestWorkerRegister:
    @pytest.mark.asyncio
    async def test_register_worker(self, client, tenant) -> None:
        resp = await client.post("/api/v1/workers/register", json={
            "name": "Mac Mini Runner",
            "labels": ["macos", "gpu"],
            "capabilities": ["claude_code", "codex"],
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] is not None
        assert "token" in data
        assert len(data["token"]) > 20

    @pytest.mark.asyncio
    async def test_register_worker_minimal(self, client, tenant) -> None:
        resp = await client.post("/api/v1/workers/register", json={"name": "Simple Runner"})
        assert resp.status_code == 201
        assert resp.json()["id"] is not None


class TestWorkerHeartbeat:
    @pytest.mark.asyncio
    async def test_heartbeat_valid_token(self, client, tenant) -> None:
        reg = await client.post("/api/v1/workers/register", json={"name": "Runner"})
        token = reg.json()["token"]

        resp = await client.post("/api/v1/workers/heartbeat", headers={"X-Worker-Token": token})
        assert resp.status_code == 200
        assert resp.json()["status"] == "online"

    @pytest.mark.asyncio
    async def test_heartbeat_invalid_token(self, client, tenant) -> None:
        resp = await client.post("/api/v1/workers/heartbeat", headers={"X-Worker-Token": "invalid-token"})
        assert resp.status_code == 401


class TestWorkerList:
    @pytest.mark.asyncio
    async def test_list_workers(self, client, tenant) -> None:
        await client.post("/api/v1/workers/register", json={"name": "Runner 1"})
        await client.post("/api/v1/workers/register", json={"name": "Runner 2"})

        resp = await client.get("/api/v1/workers")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    @pytest.mark.asyncio
    async def test_list_workers_empty(self, client, tenant) -> None:
        resp = await client.get("/api/v1/workers")
        assert resp.status_code == 200
        assert resp.json() == []


class TestWorkerDeregister:
    @pytest.mark.asyncio
    async def test_deregister_worker(self, client, tenant) -> None:
        reg = await client.post("/api/v1/workers/register", json={"name": "Runner"})
        worker_id = reg.json()["id"]

        resp = await client.delete(f"/api/v1/workers/{worker_id}")
        assert resp.status_code == 204

        # Should not appear in list anymore
        list_resp = await client.get("/api/v1/workers")
        assert len(list_resp.json()) == 0

    @pytest.mark.asyncio
    async def test_deregister_not_found(self, client, tenant) -> None:
        resp = await client.delete(f"/api/v1/workers/{uuid.uuid4()}")
        assert resp.status_code == 404
