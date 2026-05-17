"""TenantMiddleware + resolve_user_tenant + tenant-id helpers."""

from __future__ import annotations

import uuid

import pytest
from bsvibe_auth import BSVibeUser
from fastapi import Request
from sqlalchemy import select

from backend.src.core.tenant_context import (
    DEFAULT_TENANT_ID,
    TenantMiddleware,
    _tenant_id_from_user,
    derive_personal_tenant_id,
    ensure_personal_tenant,
    get_tenant_id,
    resolve_user_tenant,
)
from backend.src.models import Tenant


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
async def test_ensure_personal_tenant_projects_new_row_when_slug_already_used(db_session):
    """When a stale row already holds this user's slug under a DIFFERENT
    tenant id, ``ensure_personal_tenant`` must still project a fresh row
    for the user's current tenant id.

    Production bug (found 2026-05-17 via web-UI dogfood): BSVibe
    reassigned a founder's tenant id; the old ``tenants`` row still held
    the slug (=user id); the old code swallowed the slug-UNIQUE
    violation and never created the new row, so every write under the
    new tenant id FK-violated and the founder could not submit a
    Direction. The slug UNIQUE constraint was dropped — both projection
    rows now coexist and the new tenant id is usable.
    """
    user_id = "u-conflict"
    target_tid = derive_personal_tenant_id(user_id)
    other_tid = uuid.uuid4()
    assert other_tid != target_tid

    db_session.add(Tenant(id=other_tid, name="legacy", slug=user_id, owner_user_id="legacy"))
    await db_session.commit()

    user = _user(id_=user_id, email="u@e")
    await ensure_personal_tenant(db_session, target_tid, user)

    # The new tenant row exists — the thing every write under it needs.
    target_row = (await db_session.execute(select(Tenant).where(Tenant.id == target_tid))).scalar_one()
    assert target_row.slug == user_id
    # The stale row is left untouched; both projections coexist.
    rows = (await db_session.execute(select(Tenant).where(Tenant.slug == user_id))).scalars().all()
    assert {r.id for r in rows} == {other_tid, target_tid}


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
async def test_middleware_does_not_trust_unverified_jwt_payload():
    """SECURITY: even when the request carries a syntactically-valid
    bearer token, the middleware must NOT use the unverified payload to
    populate ``request.state.tenant_id``. The verified value is set by
    ``get_current_user`` after signature verification.
    """
    import base64
    import json

    captured = {}
    spoofed_tid = uuid.uuid4()
    header = base64.urlsafe_b64encode(b'{"alg":"HS256"}').rstrip(b"=").decode()
    body = (
        base64.urlsafe_b64encode(
            json.dumps({"sub": "attacker", "app_metadata": {"tenant_id": str(spoofed_tid)}}).encode()
        )
        .rstrip(b"=")
        .decode()
    )
    token = f"{header}.{body}.junk"

    async def app(scope, receive, send):
        req = Request(scope)
        captured["tid"] = req.state.tenant_id

    mw = TenantMiddleware(app)
    scope = {
        "type": "http",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
        "state": {},
    }
    await mw(scope, lambda: None, lambda m: None)
    # Spoofed tenant_id from the payload must NOT have been stamped.
    assert captured["tid"] == DEFAULT_TENANT_ID
    assert captured["tid"] != spoofed_tid


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
