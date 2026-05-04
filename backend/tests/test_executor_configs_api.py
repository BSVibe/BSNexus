"""ExecutorConfigs API contract tests."""

from __future__ import annotations

import uuid

import pytest


AUTH = {"Authorization": "Bearer fake"}


@pytest.mark.asyncio
async def test_list_empty(client):
    resp = await client.get("/api/v1/executor-configs", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_create_returns_201_and_scopes_to_tenant(client, mock_tenant_id):
    resp = await client.post(
        "/api/v1/executor-configs",
        json={
            "name": "GPT-4o",
            "executor_type": "llm_api",
            "config": {"model": "openai/gpt-4o", "api_key": "sk-test"},
            "description": "Default LLM",
            "is_selected": True,
        },
        headers=AUTH,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "GPT-4o"
    assert body["executor_type"] == "llm_api"
    assert body["is_selected"] is True
    assert body["tenant_id"] == str(mock_tenant_id)
    assert body["config"]["model"] == "openai/gpt-4o"


@pytest.mark.asyncio
async def test_create_rejects_unknown_executor_type(client):
    resp = await client.post(
        "/api/v1/executor-configs",
        json={"name": "Bogus", "executor_type": "madeup", "config": {}},
        headers=AUTH,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_rejects_empty_name(client):
    resp = await client.post(
        "/api/v1/executor-configs",
        json={"name": "", "executor_type": "llm_api", "config": {}},
        headers=AUTH,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_selected_is_unique_per_tenant(client):
    first = await client.post(
        "/api/v1/executor-configs",
        json={
            "name": "A",
            "executor_type": "llm_api",
            "config": {},
            "is_selected": True,
        },
        headers=AUTH,
    )
    assert first.status_code == 201

    second = await client.post(
        "/api/v1/executor-configs",
        json={
            "name": "B",
            "executor_type": "bsgateway",
            "config": {},
            "is_selected": True,
        },
        headers=AUTH,
    )
    assert second.status_code == 201

    rows = (await client.get("/api/v1/executor-configs", headers=AUTH)).json()
    selected = [r for r in rows if r["is_selected"]]
    # Only the most recent one stays selected.
    assert len(selected) == 1
    assert selected[0]["name"] == "B"


@pytest.mark.asyncio
async def test_get_by_id(client):
    created = (
        await client.post(
            "/api/v1/executor-configs",
            json={"name": "X", "executor_type": "llm_api", "config": {}},
            headers=AUTH,
        )
    ).json()

    resp = await client.get(f"/api/v1/executor-configs/{created['id']}", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]


@pytest.mark.asyncio
async def test_get_404_for_foreign_tenant(client, db_session):
    from backend.src.models import ExecutorConfig, Tenant

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
    row = ExecutorConfig(
        tenant_id=other_tid,
        name="Hidden",
        executor_type="llm_api",
        config={},
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)

    resp = await client.get(f"/api/v1/executor-configs/{row.id}", headers=AUTH)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_patch_updates_and_unsets_others_when_making_selected(client):
    a = (
        await client.post(
            "/api/v1/executor-configs",
            json={
                "name": "A",
                "executor_type": "llm_api",
                "config": {},
                "is_selected": True,
            },
            headers=AUTH,
        )
    ).json()
    b = (
        await client.post(
            "/api/v1/executor-configs",
            json={
                "name": "B",
                "executor_type": "bsgateway",
                "config": {},
                "is_selected": False,
            },
            headers=AUTH,
        )
    ).json()

    patched = await client.patch(
        f"/api/v1/executor-configs/{b['id']}",
        json={"is_selected": True, "description": "now selected"},
        headers=AUTH,
    )
    assert patched.status_code == 200
    assert patched.json()["is_selected"] is True

    a_refetched = (await client.get(f"/api/v1/executor-configs/{a['id']}", headers=AUTH)).json()
    assert a_refetched["is_selected"] is False


@pytest.mark.asyncio
async def test_delete(client):
    created = (
        await client.post(
            "/api/v1/executor-configs",
            json={"name": "Temp", "executor_type": "llm_api", "config": {}},
            headers=AUTH,
        )
    ).json()

    resp = await client.delete(f"/api/v1/executor-configs/{created['id']}", headers=AUTH)
    assert resp.status_code == 204

    resp = await client.get(f"/api/v1/executor-configs/{created['id']}", headers=AUTH)
    assert resp.status_code == 404


# ─── api_key encryption (PR #42 #11) ──────────────────────────────────


@pytest.mark.asyncio
async def test_create_with_explicit_api_key_encrypts_and_redacts(client, db_session):
    """``api_key`` field on the payload → stored on encrypted column,
    never round-tripped via response, never visible in DB row's
    ``config`` dict."""
    from backend.src.models import ExecutorConfig

    resp = await client.post(
        "/api/v1/executor-configs",
        json={
            "name": "BSGW Prod",
            "executor_type": "bsgateway",
            "config": {"bsgateway_url": "https://gw.example"},
            "api_key": "real-secret-token-xyz",
            "is_selected": True,
        },
        headers=AUTH,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["has_api_key"] is True
    # Response config does NOT include the raw key, even via legacy
    # field name.
    assert "api_key" not in body["config"]
    assert "bsgateway_api_key" not in body["config"]
    assert "real-secret-token-xyz" not in resp.text

    # DB row has the encrypted column populated; ``config`` JSON has no
    # plaintext leftovers.
    row = await db_session.get(ExecutorConfig, uuid.UUID(body["id"]))
    assert row is not None
    assert row.api_key_encrypted is not None
    assert "real-secret-token-xyz" not in row.api_key_encrypted
    assert "api_key" not in (row.config or {})
    assert "bsgateway_api_key" not in (row.config or {})


@pytest.mark.asyncio
async def test_create_with_legacy_config_api_key_is_lifted_and_redacted(client, db_session):
    """Backwards compat — pre-2026-05-04 callers passed the secret as
    ``config.bsgateway_api_key``. The API still accepts that, lifts it
    to the encrypted column, and strips it from the JSON config."""
    from backend.src.models import ExecutorConfig

    resp = await client.post(
        "/api/v1/executor-configs",
        json={
            "name": "BSGW Legacy",
            "executor_type": "bsgateway",
            "config": {
                "bsgateway_url": "https://gw.example",
                "bsgateway_api_key": "legacy-token-abc",
            },
        },
        headers=AUTH,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["has_api_key"] is True
    assert "bsgateway_api_key" not in body["config"]

    row = await db_session.get(ExecutorConfig, uuid.UUID(body["id"]))
    assert row.api_key_encrypted is not None
    assert "legacy-token-abc" not in row.api_key_encrypted
    assert "bsgateway_api_key" not in (row.config or {})


@pytest.mark.asyncio
async def test_patch_api_key_only_rotates_secret(client, db_session):
    """PATCH with just ``api_key`` re-encrypts without disturbing
    other config keys."""
    from backend.src.models import ExecutorConfig

    created = (
        await client.post(
            "/api/v1/executor-configs",
            json={
                "name": "Rotate",
                "executor_type": "bsgateway",
                "config": {"bsgateway_url": "https://gw.example"},
                "api_key": "old-secret",
            },
            headers=AUTH,
        )
    ).json()
    row = await db_session.get(ExecutorConfig, uuid.UUID(created["id"]))
    old_encrypted = row.api_key_encrypted

    patched = await client.patch(
        f"/api/v1/executor-configs/{created['id']}",
        json={"api_key": "new-secret"},
        headers=AUTH,
    )
    assert patched.status_code == 200

    await db_session.refresh(row)
    assert row.api_key_encrypted != old_encrypted
    assert (row.config or {}).get("bsgateway_url") == "https://gw.example"


@pytest.mark.asyncio
async def test_response_never_contains_encrypted_blob(client):
    """The encrypted ciphertext must not surface in the API response —
    only ``has_api_key: bool``."""
    resp = await client.post(
        "/api/v1/executor-configs",
        json={
            "name": "Hidden",
            "executor_type": "bsgateway",
            "config": {"bsgateway_url": "https://x"},
            "api_key": "ssh-very-secret",
        },
        headers=AUTH,
    )
    body = resp.json()
    assert "api_key_encrypted" not in body
    assert "ssh-very-secret" not in resp.text
