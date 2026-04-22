from __future__ import annotations

import pytest
from httpx import AsyncClient

from backend.src.api.settings import mask_api_key


@pytest.mark.asyncio
async def test_get_settings_empty(client: AsyncClient) -> None:
    """GET /api/v1/settings with no data returns all None."""
    resp = await client.get("/api/v1/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["llm_api_key"] is None
    assert data["llm_model"] is None
    assert data["llm_base_url"] is None


@pytest.mark.asyncio
async def test_update_settings_model(client: AsyncClient) -> None:
    """PUT /api/v1/settings updates llm_model and returns it."""
    resp = await client.put(
        "/api/v1/settings",
        json={"llm_model": "anthropic/claude-sonnet-4-20250514"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["llm_model"] == "anthropic/claude-sonnet-4-20250514"


@pytest.mark.asyncio
async def test_update_settings_base_url(client: AsyncClient) -> None:
    """PUT /api/v1/settings updates llm_base_url and returns it."""
    resp = await client.put(
        "/api/v1/settings",
        json={"llm_base_url": "https://api.example.com"},
    )
    assert resp.status_code == 200
    assert resp.json()["llm_base_url"] == "https://api.example.com"


@pytest.mark.asyncio
async def test_api_key_masked(client: AsyncClient) -> None:
    """API key is masked when returned via GET."""
    await client.put(
        "/api/v1/settings",
        json={"llm_api_key": "sk-ant-abcdefghijklmnop"},
    )
    resp = await client.get("/api/v1/settings")
    assert resp.status_code == 200
    data = resp.json()
    # Must not reveal the full key
    assert "sk-ant-abcdefghijklmnop" not in data["llm_api_key"]
    # Must start with first 3 chars
    assert data["llm_api_key"].startswith("sk-")
    # Must end with last 4 chars
    assert data["llm_api_key"].endswith("mnop")


@pytest.mark.asyncio
async def test_update_settings_upsert(client: AsyncClient) -> None:
    """PUT /api/v1/settings performs upsert: create, then update."""
    # Create
    resp1 = await client.put(
        "/api/v1/settings",
        json={"llm_model": "gpt-4o"},
    )
    assert resp1.status_code == 200
    assert resp1.json()["llm_model"] == "gpt-4o"

    # Update
    resp2 = await client.put(
        "/api/v1/settings",
        json={"llm_model": "anthropic/claude-sonnet-4-20250514"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["llm_model"] == "anthropic/claude-sonnet-4-20250514"


@pytest.mark.asyncio
async def test_update_settings_partial(client: AsyncClient) -> None:
    """PUT /api/v1/settings only updates provided fields, leaves others intact."""
    # Set model first
    await client.put(
        "/api/v1/settings",
        json={"llm_model": "gpt-4o"},
    )

    # Update only base_url
    resp = await client.put(
        "/api/v1/settings",
        json={"llm_base_url": "https://api.openai.com"},
    )
    assert resp.status_code == 200
    data = resp.json()
    # Previously set model should still be there
    assert data["llm_model"] == "gpt-4o"
    assert data["llm_base_url"] == "https://api.openai.com"


@pytest.mark.asyncio
async def test_update_settings_null_ignored(client: AsyncClient) -> None:
    """PUT /api/v1/settings with null value does not overwrite existing."""
    # Set model first
    await client.put(
        "/api/v1/settings",
        json={"llm_model": "gpt-4o"},
    )

    # Send null for model
    resp = await client.put(
        "/api/v1/settings",
        json={"llm_model": None},
    )
    assert resp.status_code == 200
    # Model should remain unchanged because None is skipped
    assert resp.json()["llm_model"] == "gpt-4o"


# ── Unit tests for mask_api_key ──────────────────────────────────────


def test_mask_api_key_normal() -> None:
    """Normal API key is masked correctly."""
    assert mask_api_key("sk-ant-abcdefghijklmnop") == "sk-****...mnop"


def test_mask_api_key_short() -> None:
    """Short key (less than 8 chars) is returned as-is."""
    assert mask_api_key("short") == "short"


def test_mask_api_key_none() -> None:
    """None returns None."""
    assert mask_api_key(None) is None


def test_mask_api_key_empty() -> None:
    """Empty string returns as-is (len < 8)."""
    assert mask_api_key("") == ""


def test_mask_api_key_exactly_8() -> None:
    """8-char key is masked."""
    result = mask_api_key("12345678")
    assert result == "123****...5678"


# ── Install token endpoints (tenant-scoped) ─────────────────────────


@pytest.fixture
async def _seed_default_tenant(db_session):
    from backend.src.core.tenant_context import DEFAULT_TENANT_ID
    from backend.src.models import Tenant

    db_session.add(
        Tenant(id=DEFAULT_TENANT_ID, name="Test", slug="test", owner_user_id="test-user")
    )
    await db_session.commit()
    yield


@pytest.mark.asyncio
async def test_install_token_initially_absent(client: AsyncClient, _seed_default_tenant) -> None:
    resp = await client.get("/api/v1/settings/install-token")
    assert resp.status_code == 200
    assert resp.json()["has_token"] is False


@pytest.mark.asyncio
async def test_install_token_create_returns_plaintext_once(
    client: AsyncClient, _seed_default_tenant
) -> None:
    resp = await client.post("/api/v1/settings/install-token")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_token"] is True
    assert body["token"] and body["token"].startswith("bsn-")

    # Subsequent GET reports the existence but does not return the plaintext.
    follow = await client.get("/api/v1/settings/install-token")
    assert follow.json()["has_token"] is True
    assert follow.json().get("token") in (None, "")


@pytest.mark.asyncio
async def test_install_token_revoke_clears_it(
    client: AsyncClient, _seed_default_tenant
) -> None:
    await client.post("/api/v1/settings/install-token")
    resp = await client.delete("/api/v1/settings/install-token")
    assert resp.status_code == 204
    follow = await client.get("/api/v1/settings/install-token")
    assert follow.json()["has_token"] is False


@pytest.mark.asyncio
async def test_resolve_install_token_tenant_returns_owner(
    client: AsyncClient, _seed_default_tenant, db_session
) -> None:
    from backend.src.api.settings import resolve_install_token_tenant
    from backend.src.core.tenant_context import DEFAULT_TENANT_ID

    create = await client.post("/api/v1/settings/install-token")
    token = create.json()["token"]

    tenant_id = await resolve_install_token_tenant(token, db_session)
    assert tenant_id == DEFAULT_TENANT_ID


@pytest.mark.asyncio
async def test_resolve_install_token_unknown_returns_none(
    client: AsyncClient, _seed_default_tenant, db_session
) -> None:
    from backend.src.api.settings import resolve_install_token_tenant

    tenant_id = await resolve_install_token_tenant("not-a-real-token", db_session)
    assert tenant_id is None
