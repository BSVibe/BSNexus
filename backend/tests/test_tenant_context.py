"""Tests for the tenant context middleware and helpers."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime, timezone

import pytest
from bsvibe_auth import BSVibeUser

from backend.src.core.tenant_context import (
    DEFAULT_TENANT_ID,
    TenantMiddleware,
    derive_personal_tenant_id,
    ensure_personal_tenant,
    _identify_from_token,
)


def _tenant_id_from_token(token: str):
    """Test shim around the new ``_identify_from_token`` helper."""
    _, tenant_id = _identify_from_token(token)
    return tenant_id
from backend.src.models import Tenant


# ── derive_personal_tenant_id ────────────────────────────────────────


def test_derive_personal_tenant_id_is_deterministic():
    a = derive_personal_tenant_id("user-123")
    b = derive_personal_tenant_id("user-123")
    assert a == b
    assert isinstance(a, uuid.UUID)


def test_derive_personal_tenant_id_differs_per_user():
    assert derive_personal_tenant_id("a") != derive_personal_tenant_id("b")


def test_derive_personal_tenant_id_not_default():
    assert derive_personal_tenant_id("any") != DEFAULT_TENANT_ID


# ── _tenant_id_from_token ────────────────────────────────────────────


def _make_jwt(payload: dict) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{header}.{body}.fake-signature"


def test_tenant_id_from_token_uses_app_metadata():
    tenant_uuid = str(uuid.uuid4())
    token = _make_jwt({"sub": "user-1", "app_metadata": {"tenant_id": tenant_uuid}})
    assert _tenant_id_from_token(token) == uuid.UUID(tenant_uuid)


def test_tenant_id_from_token_falls_back_to_sub():
    token = _make_jwt({"sub": "user-2"})
    assert _tenant_id_from_token(token) == derive_personal_tenant_id("user-2")


def test_tenant_id_from_token_returns_none_for_garbage():
    assert _tenant_id_from_token("not-a-jwt") is None


def test_tenant_id_from_token_ignores_invalid_claim_uuid():
    token = _make_jwt({"sub": "user-3", "app_metadata": {"tenant_id": "not-a-uuid"}})
    # Bad claim -> falls through to sub-derived id.
    assert _tenant_id_from_token(token) == derive_personal_tenant_id("user-3")


# ── ensure_personal_tenant ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_ensure_personal_tenant_inserts_row(db_session):
    user_id = "user-ensure-1"
    tenant_id = derive_personal_tenant_id(user_id)
    user = BSVibeUser(id=user_id, email="user@example.com")
    await ensure_personal_tenant(db_session, tenant_id, user)

    from sqlalchemy import select

    result = await db_session.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    assert tenant is not None
    assert tenant.id == tenant_id
    assert tenant.owner_user_id == user_id


@pytest.mark.asyncio
async def test_ensure_personal_tenant_is_idempotent(db_session):
    user_id = "user-ensure-2"
    tenant_id = derive_personal_tenant_id(user_id)
    user = BSVibeUser(id=user_id, email="user@example.com")
    await ensure_personal_tenant(db_session, tenant_id, user)
    await ensure_personal_tenant(db_session, tenant_id, user)  # second call must not raise


# ── TenantMiddleware ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tenant_middleware_stamps_request_state_from_jwt():
    captured: dict = {}

    async def fake_app(scope, receive, send):
        from fastapi import Request

        captured["state"] = Request(scope).state.tenant_id

    middleware = TenantMiddleware(fake_app)

    user_id = "user-mw-1"
    token = _make_jwt({"sub": user_id})
    scope = {
        "type": "http",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
    }

    async def noop_recv():
        return {}

    async def noop_send(_msg):
        return None

    await middleware(scope, noop_recv, noop_send)
    assert captured["state"] == derive_personal_tenant_id(user_id)


@pytest.mark.asyncio
async def test_tenant_middleware_falls_back_to_default_without_token():
    captured: dict = {}

    async def fake_app(scope, receive, send):
        from fastapi import Request

        captured["state"] = Request(scope).state.tenant_id

    middleware = TenantMiddleware(fake_app)
    scope = {"type": "http", "headers": []}

    async def noop_recv():
        return {}

    async def noop_send(_msg):
        return None

    await middleware(scope, noop_recv, noop_send)
    assert captured["state"] == DEFAULT_TENANT_ID
