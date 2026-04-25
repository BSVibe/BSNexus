"""Worker binary endpoints — register / heartbeat / poll / result.

These are hit by the ``bsnexus-worker`` CLI, not by browser users.
They authenticate via ``X-Install-Token`` (for register) or
``X-Worker-Token`` (for heartbeat / poll / result). No Bearer JWT.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

import pytest


def _sha(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _mint_install_token(db_session, tenant_id) -> str:
    from sqlalchemy import select

    from backend.src.models import Tenant

    tenant = (await db_session.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()
    raw = "install-raw-" + uuid.uuid4().hex
    tenant.worker_install_token_hash = _sha(raw)
    await db_session.commit()
    return raw


async def _register_worker_row(db_session, tenant_id, *, capabilities=None):
    from backend.src.models import Worker

    raw = "worker-raw-" + uuid.uuid4().hex
    worker = Worker(
        tenant_id=tenant_id,
        name="test-worker",
        labels=[],
        capabilities=capabilities or ["claude_code"],
        status="online",
        last_heartbeat=datetime.now(timezone.utc),
        token_hash=_sha(raw),
        is_active=True,
    )
    db_session.add(worker)
    await db_session.commit()
    await db_session.refresh(worker)
    return worker, raw


# ─── register ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_register_rejects_missing_install_token(client):
    resp = await client.post(
        "/api/v1/workers/register",
        json={"name": "host", "capabilities": ["claude_code"]},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_register_rejects_unknown_install_token(client):
    resp = await client.post(
        "/api/v1/workers/register",
        json={"name": "host", "capabilities": ["claude_code"]},
        headers={"X-Install-Token": "nope"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_register_creates_worker_and_returns_token_once(client, db_session, mock_tenant_id):
    token = await _mint_install_token(db_session, mock_tenant_id)
    resp = await client.post(
        "/api/v1/workers/register",
        json={
            "name": "host-a",
            "capabilities": ["claude_code", "codex"],
            "labels": ["project:foo"],
        },
        headers={"X-Install-Token": token},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "id" in body
    assert "token" in body
    raw_token = body["token"]
    # Never store raw; only hash.
    assert len(raw_token) >= 32

    from sqlalchemy import select

    from backend.src.models import Worker

    stmt = select(Worker).where(Worker.id == uuid.UUID(body["id"]))
    worker = (await db_session.execute(stmt)).scalar_one()
    assert worker.tenant_id == mock_tenant_id
    assert worker.name == "host-a"
    assert worker.capabilities == ["claude_code", "codex"]
    assert worker.labels == ["project:foo"]
    assert worker.token_hash == _sha(raw_token)
    # Raw token must NOT appear in stored fields.
    assert raw_token not in str(worker.token_hash)


# ─── heartbeat ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_heartbeat_rejects_missing_token(client):
    resp = await client.post("/api/v1/workers/heartbeat")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_heartbeat_rejects_unknown_token(client):
    resp = await client.post(
        "/api/v1/workers/heartbeat",
        headers={"X-Worker-Token": "garbage"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_heartbeat_updates_last_heartbeat_and_flips_online(client, db_session, mock_tenant_id):
    worker, raw = await _register_worker_row(db_session, mock_tenant_id)
    # Force offline so we can observe the flip.
    worker.status = "offline"
    await db_session.commit()

    resp = await client.post(
        "/api/v1/workers/heartbeat",
        headers={"X-Worker-Token": raw},
    )
    assert resp.status_code == 204

    await db_session.refresh(worker)
    assert worker.status == "online"
    assert worker.last_heartbeat is not None


# ─── poll ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_poll_returns_empty_list_when_no_messages(client, db_session, mock_tenant_id, mock_stream_manager):
    _, raw = await _register_worker_row(db_session, mock_tenant_id)
    mock_stream_manager.consume.return_value = []
    resp = await client.post(
        "/api/v1/workers/poll",
        headers={"X-Worker-Token": raw},
    )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_poll_maps_stream_payload_to_task_dict(client, db_session, mock_tenant_id, mock_stream_manager):
    worker, raw = await _register_worker_row(db_session, mock_tenant_id)
    run_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    mock_stream_manager.consume.return_value = [
        {
            "run_id": run_id,
            "project_id": project_id,
            "action": "execute",
            "system_prompt": "You are a helpful assistant. Do X.",
            "tools_allowed": '["read", "write"]',
            "_message_id": "1700000000000-0",
        }
    ]
    resp = await client.post(
        "/api/v1/workers/poll?count=5",
        headers={"X-Worker-Token": raw},
    )
    assert resp.status_code == 200
    tasks = resp.json()
    assert len(tasks) == 1
    task = tasks[0]
    # Backend's run_id is surfaced to the worker as task_id.
    assert task["task_id"] == run_id
    assert task["project_id"] == project_id
    assert task["action"] == "execute"
    assert task["system_prompt"].startswith("You are a helpful")
    assert task["tools_allowed"] == ["read", "write"]
    # consumer group addressed the right stream
    name, group = mock_stream_manager.consume.await_args.args[:2]
    assert name == f"runs:worker:{worker.id}"
    assert group == f"worker-{worker.id}"


@pytest.mark.asyncio
async def test_poll_rejects_unknown_token(client):
    resp = await client.post(
        "/api/v1/workers/poll",
        headers={"X-Worker-Token": "garbage"},
    )
    assert resp.status_code == 401


# ─── result ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_result_publishes_to_runs_results_stream(client, db_session, mock_tenant_id, mock_stream_manager):
    worker, raw = await _register_worker_row(db_session, mock_tenant_id)
    task_id = str(uuid.uuid4())
    resp = await client.post(
        "/api/v1/workers/result",
        json={
            "task_id": task_id,
            "success": True,
            "output_data": {"stdout": "done"},
        },
        headers={"X-Worker-Token": raw},
    )
    assert resp.status_code == 204
    mock_stream_manager.publish.assert_awaited()
    stream_name, payload = mock_stream_manager.publish.await_args.args
    assert stream_name == "runs:results"
    assert payload["run_id"] == task_id
    assert payload["worker_id"] == str(worker.id)
    assert payload["success"] == "true"
    assert payload["output_data"] == {"stdout": "done"}


@pytest.mark.asyncio
async def test_result_failure_includes_error_message(client, db_session, mock_tenant_id, mock_stream_manager):
    _, raw = await _register_worker_row(db_session, mock_tenant_id)
    resp = await client.post(
        "/api/v1/workers/result",
        json={
            "task_id": str(uuid.uuid4()),
            "success": False,
            "error_message": "cli not found",
        },
        headers={"X-Worker-Token": raw},
    )
    assert resp.status_code == 204
    _, payload = mock_stream_manager.publish.await_args.args
    assert payload["success"] == "false"
    assert payload["error_message"] == "cli not found"


@pytest.mark.asyncio
async def test_result_rejects_unknown_token(client):
    resp = await client.post(
        "/api/v1/workers/result",
        json={"task_id": str(uuid.uuid4()), "success": True},
        headers={"X-Worker-Token": "garbage"},
    )
    assert resp.status_code == 401
