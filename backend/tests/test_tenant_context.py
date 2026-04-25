"""TenantMiddleware + resolve_user_tenant + identify_from_token."""

from __future__ import annotations

import base64
import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from bsvibe_auth import BSVibeUser
from fastapi import Request
from sqlalchemy import select

from backend.src.core.tenant_context import (
    DEFAULT_TENANT_ID,
    TenantMiddleware,
    _decode_jwt_payload,
    _identify_from_token,
    _tenant_id_from_payload,
    _tenant_id_from_user,
    derive_personal_tenant_id,
    ensure_personal_tenant,
    get_tenant_id,
    resolve_user_tenant,
)
from backend.src.models import Tenant


def _make_jwt(payload: dict) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"HS256"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{header}.{body}.signature"


def _user(id_="u1", email="u@example.com", tenant_id=None, role="viewer") -> BSVibeUser:
    meta: dict = {"role": role}
    if tenant_id is not None:
        meta["tenant_id"] = str(tenant_id)
    return BSVibeUser(id=id_, email=email, app_metadata=meta, user_metadata={})


def test_derive_personal_tenant_id_is_stable():
    a = derive_personal_tenant_id("user-1")
    b = derive_personal_tenant_id("user-1")
    c = derive_personal_tenant_id("user-2")
    assert a == b
    assert a != c


def test_tenant_id_from_user_uses_explicit_claim():
    tid = uuid.uuid4()
    assert _tenant_id_from_user(_user(tenant_id=tid)) == tid


def test_tenant_id_from_user_derives_from_id_when_no_claim():
    assert _tenant_id_from_user(_user(id_="u1")) == derive_personal_tenant_id("u1")


def test_tenant_id_from_user_falls_back_on_invalid_claim(capsys):
    user = BSVibeUser(
        id="u1",
        email="e",
        app_metadata={"tenant_id": "not-a-uuid"},
        user_metadata={},
    )
    assert _tenant_id_from_user(user) == derive_personal_tenant_id("u1")


def test_tenant_id_from_user_returns_none_when_no_id():
    user = BSVibeUser(id="", email=None, app_metadata={}, user_metadata={})
    assert _tenant_id_from_user(user) is None


def test_tenant_id_from_user_none_input():
    assert _tenant_id_from_user(None) is None


def test_decode_jwt_payload_rejects_malformed():
    assert _decode_jwt_payload("not.a.jwt.extra") is None
    assert _decode_jwt_payload("single-segment") is None


def test_decode_jwt_payload_happy_path():
    token = _make_jwt({"sub": "abc", "email": "x@y"})
    payload = _decode_jwt_payload(token)
    assert payload == {"sub": "abc", "email": "x@y"}


def test_tenant_id_from_payload_explicit_claim():
    tid = uuid.uuid4()
    out = _tenant_id_from_payload({"app_metadata": {"tenant_id": str(tid)}})
    assert out == tid


def test_tenant_id_from_payload_derives_from_sub():
    out = _tenant_id_from_payload({"sub": "user-x"})
    assert out == derive_personal_tenant_id("user-x")


def test_tenant_id_from_payload_none_when_no_info():
    assert _tenant_id_from_payload({}) is None
    assert _tenant_id_from_payload({"app_metadata": {"tenant_id": "bad"}, "sub": None}) is None


def test_identify_from_token_invalid_token():
    with patch("backend.src.config.settings") as s:
        s.e2e_test_token = ""
        user, tid = _identify_from_token("malformed")
        assert user is None
        assert tid is None


def test_identify_from_token_e2e_bypass():
    with patch("backend.src.config.settings") as s:
        s.e2e_test_token = "dev-token"
        s.e2e_test_user_tenant_id = "11111111-1111-4111-8111-111111111111"
        s.e2e_test_user_id = "tester"
        s.e2e_test_user_email = "t@e"
        user, tid = _identify_from_token("dev-token")
        assert user is not None
        assert user.id == "tester"
        assert str(tid) == "11111111-1111-4111-8111-111111111111"


def test_identify_from_token_jwt_path():
    tid = uuid.uuid4()
    token = _make_jwt({"sub": "real-user", "app_metadata": {"tenant_id": str(tid), "role": "admin"}})
    with patch("backend.src.config.settings") as s:
        s.e2e_test_token = ""
        user, found_tid = _identify_from_token(token)
        assert user is not None
        assert user.id == "real-user"
        assert user.app_metadata["role"] == "admin"
        assert found_tid == tid


def test_get_tenant_id_reads_request_state():
    tid = uuid.uuid4()
    req = Request({"type": "http", "headers": []})
    req.state.tenant_id = tid
    assert get_tenant_id(req) == tid


def test_get_tenant_id_falls_back_to_default():
    req = Request({"type": "http", "headers": []})
    assert get_tenant_id(req) == DEFAULT_TENANT_ID


@pytest.mark.asyncio
async def test_ensure_personal_tenant_inserts_and_updates(db_session):
    tid = uuid.uuid4()
    user = _user(id_="u1", email="first@e")

    await ensure_personal_tenant(db_session, tid, user)
    row = (await db_session.execute(select(Tenant).where(Tenant.id == tid))).scalar_one()
    assert row.name == "first@e"

    user.email = "second@e"
    await ensure_personal_tenant(db_session, tid, user)
    row = (await db_session.execute(select(Tenant).where(Tenant.id == tid))).scalar_one()
    assert row.name == "second@e"


@pytest.mark.asyncio
async def test_resolve_user_tenant_stamps_request_and_upserts(db_session):
    tid = uuid.uuid4()
    user = _user(id_="u1", email="u@e", tenant_id=tid)
    req = Request({"type": "http", "headers": []})

    out = await resolve_user_tenant(req, user, db_session)
    assert out == tid
    assert req.state.tenant_id == tid
    row = (await db_session.execute(select(Tenant).where(Tenant.id == tid))).scalar_one_or_none()
    assert row is not None


@pytest.mark.asyncio
async def test_resolve_user_tenant_default_when_user_id_empty(db_session):
    user = BSVibeUser(id="", email=None, app_metadata={}, user_metadata={})
    req = Request({"type": "http", "headers": []})
    out = await resolve_user_tenant(req, user, db_session)
    assert out == DEFAULT_TENANT_ID


@pytest.mark.asyncio
async def test_middleware_skips_non_http_scopes():
    called = []

    async def app(scope, receive, send):
        called.append(scope["type"])

    mw = TenantMiddleware(app)
    await mw({"type": "lifespan"}, lambda: None, lambda m: None)
    assert called == ["lifespan"]


@pytest.mark.asyncio
async def test_middleware_stamps_default_without_authorization():
    captured = {}

    async def app(scope, receive, send):
        req = Request(scope)
        captured["tid"] = req.state.tenant_id

    mw = TenantMiddleware(app)
    scope = {"type": "http", "headers": [], "state": {}}
    await mw(scope, lambda: None, lambda m: None)
    assert captured["tid"] == DEFAULT_TENANT_ID


@pytest.mark.asyncio
async def test_middleware_stamps_tenant_from_bearer_token():
    captured = {}
    tid = uuid.uuid4()
    token = _make_jwt({"sub": "u1", "app_metadata": {"tenant_id": str(tid)}})

    async def app(scope, receive, send):
        req = Request(scope)
        captured["tid"] = req.state.tenant_id

    mw = TenantMiddleware(app)
    scope = {
        "type": "http",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
        "state": {},
    }
    with patch(
        "backend.src.core.tenant_context._upsert_tenant_for_request",
        AsyncMock(),
    ):
        await mw(scope, lambda: None, lambda m: None)
    assert captured["tid"] == tid


@pytest.mark.asyncio
async def test_middleware_keeps_default_when_token_is_malformed():
    captured = {}

    async def app(scope, receive, send):
        req = Request(scope)
        captured["tid"] = req.state.tenant_id

    mw = TenantMiddleware(app)
    scope = {
        "type": "http",
        "headers": [(b"authorization", b"Bearer garbage")],
        "state": {},
    }
    await mw(scope, lambda: None, lambda m: None)
    assert captured["tid"] == DEFAULT_TENANT_ID
