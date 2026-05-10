"""Tests for the greenfield executor-config admin (G7.5b + G7.5e).

CLAUDE.md MUST rule "Two-path LLM dispatch":
  - executor_type=bsgateway → BSGateway (SaaS by default)
  - executor_type=llm_api   → litellm direct (provider URL + model + key)

This endpoint is the per-tenant config:
  GET  /api/v1/executor-config       — null if not configured, else redacted view
  PUT  /api/v1/executor-config       — upsert with kind / fields / api_key

G7.5e: the ``enabled`` toggle is gone — existence of the row IS the
activation signal. ``api_key`` is optional for bsgateway (SSO covers
auth on the SaaS path).

Secrets at rest: api_key_encrypted via EncryptionManager. Wire shape
exposes ``has_api_key`` only.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from backend.src.config import settings as app_settings
from backend.src.core.encryption import EncryptionManager
from backend.src.models.executor_config import ExecutorConfig, ExecutorKind


@pytest.mark.asyncio
async def test_get_executor_config_returns_null_when_not_configured(client, mock_tenant_id, seeded_tenant):
    resp = await client.get(
        "/api/v1/executor-config",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() is None


@pytest.mark.asyncio
async def test_put_executor_config_creates_llm_api_config(client, db_session, mock_tenant_id, seeded_tenant):
    resp = await client.put(
        "/api/v1/executor-config",
        json={
            "kind": "llm_api",
            "base_url": "http://host.docker.internal:11434",
            "model": "ollama_chat/qwen3-coder:30b",
            "api_key": "secret-token-for-ollama-proxy",
        },
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "llm_api"
    assert body["base_url"] == "http://host.docker.internal:11434"
    assert body["model"] == "ollama_chat/qwen3-coder:30b"
    assert body["has_api_key"] is True
    assert "enabled" not in body
    assert "api_key" not in body
    assert "api_key_encrypted" not in body

    row = (
        await db_session.execute(select(ExecutorConfig).where(ExecutorConfig.tenant_id == mock_tenant_id))
    ).scalar_one()
    assert row.kind == ExecutorKind.llm_api
    enc = EncryptionManager(app_settings.encryption_key)
    assert enc.decrypt_value(row.api_key_encrypted) == "secret-token-for-ollama-proxy"


@pytest.mark.asyncio
async def test_put_bsgateway_config_works_without_api_key(client, db_session, mock_tenant_id, seeded_tenant):
    """G7.5e — BSGateway SaaS uses SSO for auth, so ``api_key`` is
    optional. The founder should be able to save a BSGateway config
    with just the URL."""
    resp = await client.put(
        "/api/v1/executor-config",
        json={
            "kind": "bsgateway",
            "base_url": "https://gateway.bsvibe.dev",
        },
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "bsgateway"
    assert body["base_url"] == "https://gateway.bsvibe.dev"
    assert body["has_api_key"] is False


@pytest.mark.asyncio
async def test_put_executor_config_preserves_api_key_when_omitted(client, db_session, mock_tenant_id, seeded_tenant):
    """Editing other fields without re-typing the api_key keeps the
    existing encrypted value."""
    enc = EncryptionManager(app_settings.encryption_key)
    db_session.add(
        ExecutorConfig(
            tenant_id=mock_tenant_id,
            kind=ExecutorKind.llm_api,
            base_url="http://localhost:11434",
            model="ollama_chat/qwen3-coder:30b",
            api_key_encrypted=enc.encrypt_value("original-key"),
        )
    )
    await db_session.commit()

    resp = await client.put(
        "/api/v1/executor-config",
        json={
            "kind": "llm_api",
            "base_url": "http://localhost:11434",
            "model": "ollama_chat/qwen3-coder:30b",
            # api_key omitted — should preserve existing
        },
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["has_api_key"] is True

    db_session.expire_all()
    row = (
        await db_session.execute(select(ExecutorConfig).where(ExecutorConfig.tenant_id == mock_tenant_id))
    ).scalar_one()
    assert enc.decrypt_value(row.api_key_encrypted) == "original-key"


@pytest.mark.asyncio
async def test_put_executor_config_clears_api_key_when_null(client, db_session, mock_tenant_id, seeded_tenant):
    """``api_key: null`` explicitly clears the stored secret."""
    enc = EncryptionManager(app_settings.encryption_key)
    db_session.add(
        ExecutorConfig(
            tenant_id=mock_tenant_id,
            kind=ExecutorKind.llm_api,
            api_key_encrypted=enc.encrypt_value("original-key"),
        )
    )
    await db_session.commit()

    resp = await client.put(
        "/api/v1/executor-config",
        json={"kind": "llm_api", "api_key": None},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["has_api_key"] is False

    db_session.expire_all()
    row = (
        await db_session.execute(select(ExecutorConfig).where(ExecutorConfig.tenant_id == mock_tenant_id))
    ).scalar_one()
    assert row.api_key_encrypted is None


@pytest.mark.asyncio
async def test_put_executor_config_switches_kind(client, db_session, mock_tenant_id, seeded_tenant):
    """Switching from llm_api to bsgateway is one PUT — the unique
    constraint is per tenant, not per (tenant, kind)."""
    enc = EncryptionManager(app_settings.encryption_key)
    db_session.add(
        ExecutorConfig(
            tenant_id=mock_tenant_id,
            kind=ExecutorKind.llm_api,
            base_url="http://localhost:11434",
            model="ollama_chat/qwen3-coder:30b",
            api_key_encrypted=enc.encrypt_value("ollama-key"),
        )
    )
    await db_session.commit()

    resp = await client.put(
        "/api/v1/executor-config",
        json={
            "kind": "bsgateway",
            "base_url": "https://gateway.bsvibe.dev",
            "model": None,
        },
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "bsgateway"
    assert body["base_url"] == "https://gateway.bsvibe.dev"
    assert body["model"] is None
    # api_key not sent → preserves existing → still has the ollama key
    assert body["has_api_key"] is True


@pytest.mark.asyncio
async def test_executor_config_is_tenant_scoped(client, db_session, mock_tenant_id, seeded_tenant):
    from backend.src.models import Tenant

    other_tenant_id = uuid.uuid4()
    db_session.add(
        Tenant(
            id=other_tenant_id,
            name="Other",
            slug=f"other-{other_tenant_id.hex[:8]}",
            owner_user_id="other-user",
        )
    )
    await db_session.flush()
    other_config = ExecutorConfig(
        tenant_id=other_tenant_id,
        kind=ExecutorKind.bsgateway,
        base_url="https://other-gateway.example",
    )
    db_session.add(other_config)
    await db_session.commit()

    resp = await client.get(
        "/api/v1/executor-config",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200
    assert resp.json() is None
